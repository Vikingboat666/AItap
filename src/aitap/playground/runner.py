"""Prompt-level playground runner.

Given a :class:`PromptSite` (the canonical scanned shape) plus a list of
dataset cases and an :class:`LLMClient`, fan out one chat call per case,
**concurrently** via ``asyncio.gather``, and collect per-case outputs.

This module is the single source of truth for "given a prompt + inputs,
produce outputs and a cost summary". It powers:

- ``POST /api/runs`` for ``target_kind="prompt"`` (the API layer wraps the
  result into :class:`aitap.server.routes.RunDetailResponse`).
- ``pipeline_runner.run_pipeline`` for the ``node`` / ``segment`` /
  ``end_to_end`` modes (each pipeline node delegates here for the actual
  LLM call).

Why a result wrapper instead of a bare ``list[RunOutput]``:
    The brief signs the contract as ``-> list[RunOutput]`` (matching the
    OpenAPI ``RunOutput`` shape) — but the API layer also needs
    ``cost_usd`` and aggregated token usage to populate
    :class:`RunDetailResponse`. We could log+stash metrics in a side
    channel, but the cleaner pattern is to return a small dataclass that
    carries both the per-case ``outputs`` list and the rolled-up metrics.
    Callers that only need the outputs read ``.outputs``; the API layer
    additionally reads ``.cost_usd`` / ``.usage``.

Error policy:
    A ``client.chat()`` raise on case *i* is captured into
    ``outputs[i].error`` rather than aborting the whole batch — a single
    flaky case shouldn't poison an otherwise-useful run. Token usage and
    cost for the *failing* case are counted as zero because no
    ChatResponse came back.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from aitap.deep.client import ChatMessage, ChatResponse, LLMClient, TokenUsage
from aitap.scanner.models import CallParameters, Message, PromptSite, Role
from aitap.server.routes import DatasetCase, RunOutput


@dataclass(frozen=True)
class PromptRunMetrics:
    """Roll-up of token usage + cost across every case in a run."""

    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float


@dataclass(frozen=True)
class PromptRunResult:
    """Bundle of per-case outputs plus the aggregated cost/usage summary."""

    outputs: list[RunOutput]
    metrics: PromptRunMetrics
    # Per-case ChatResponses kept around for callers (e.g., pipeline runner)
    # that need to thread the assistant text through downstream nodes.
    # Index aligns with ``outputs``; ``None`` means the call errored.
    responses: list[ChatResponse | None] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return self.metrics.total_cost_usd

    @property
    def usage(self) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.metrics.total_input_tokens,
            output_tokens=self.metrics.total_output_tokens,
        )


# `{var}` matches Python f-string placeholders and jinja-style `{{var}}`.
# We accept both because PromptSite.messages.template_kind can be FSTRING
# (single-brace) or JINJA2 (double-brace); literal templates fall back to
# the f-string rule which is the more permissive of the two.
_PLACEHOLDER_RE = re.compile(r"\{\{?\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\}?\}")


def _render_template(template_text: str, inputs: dict[str, object]) -> str:
    """Fill ``{name}`` / ``{{name}}`` placeholders from ``inputs``.

    Missing keys are left as-is — production code should validate, but a
    playground runner deliberately tolerates partial inputs so the user
    can see "ah, I forgot {tone}" in the rendered output rather than
    crash before the LLM is even called.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in inputs:
            return match.group(0)
        return str(inputs[name])

    return _PLACEHOLDER_RE.sub(_sub, template_text)


def _to_chat_role(role: Role) -> str:
    """PromptSite uses an enum; ChatMessage uses literal strings.

    Keeping the mapping explicit means a future Role addition fails type
    checking here rather than silently passing through an invalid role
    string to a provider that rejects it.
    """
    mapping: dict[Role, str] = {
        Role.SYSTEM: "system",
        Role.USER: "user",
        Role.ASSISTANT: "assistant",
        Role.TOOL: "tool",
    }
    return mapping[role]


def _build_chat_messages(messages: list[Message], inputs: dict[str, object]) -> list[ChatMessage]:
    """Render each message's template with the case's inputs."""
    return [
        ChatMessage(
            role=_to_chat_role(message.role),  # type: ignore[arg-type]
            content=_render_template(message.template_text, inputs),
        )
        for message in messages
    ]


def _coerce_response_format(value: str | None) -> str | None:
    """CallParameters.response_format is `str|None` (open-ended); the
    LLMClient contract narrows it to ``"text"`` | ``"json"`` | ``None``.

    Unknown values (e.g., ``"json_schema"``) are downgraded to ``None`` so
    we don't pass an invalid literal into the client — providers that
    support richer formats will still get the user's prompt text.
    """
    if value in ("text", "json"):
        return value
    return None


async def _execute_case(
    *,
    case_index: int,
    chat_messages: list[ChatMessage],
    client: LLMClient,
    parameters: CallParameters,
) -> tuple[RunOutput, ChatResponse | None]:
    """Run a single case and translate exceptions into a `RunOutput.error`.

    Catching ``Exception`` (rather than letting it propagate to
    ``asyncio.gather``) is intentional: ``return_exceptions=True`` on
    gather would buy us the same isolation but loses the per-case index
    binding and forces every caller to re-walk the result list. Doing it
    here keeps ``outputs[i]`` aligned 1:1 with ``dataset_cases[i]``.
    """
    response_format = _coerce_response_format(parameters.response_format)
    try:
        response = await client.chat(
            chat_messages,
            temperature=parameters.temperature,
            max_tokens=parameters.max_tokens,
            top_p=parameters.top_p,
            response_format=response_format,  # type: ignore[arg-type]
        )
    except Exception as exc:
        return (
            RunOutput(case_index=case_index, error=f"{type(exc).__name__}: {exc}"),
            None,
        )
    return (
        RunOutput(case_index=case_index, text=response.text),
        response,
    )


async def run_prompt(
    site: PromptSite,
    version: int,
    dataset_cases: list[DatasetCase],
    client: LLMClient,
    parameters: CallParameters,
) -> PromptRunResult:
    """Execute ``site`` against every case concurrently and return the bundle.

    Args:
        site: Scanned prompt definition. Used for its ``messages`` (which
            are templated against each case's ``inputs``).
        version: Prompt version number recorded against the run. Currently
            unused inside this function (the store layer keys runs by
            version), but kept on the signature so callers don't drift
            when we start logging it; the ``# noqa: ARG001`` documents the
            intentional shape rather than a forgotten parameter.
        dataset_cases: One LLM call is dispatched per case, **in parallel**.
        client: Any concrete :class:`LLMClient` — Anthropic, OpenAI, or
            :class:`MockLLMClient` for offline tests.
        parameters: Call-time knobs (temperature, max_tokens, etc.). When
            the user has overridden the static ``site.parameters`` from
            the UI, the caller passes the merged result here.

    Returns:
        A :class:`PromptRunResult` whose ``outputs`` field is the
        list[RunOutput] in the OpenAPI shape, plus aggregated cost and
        token metrics for the API layer.
    """
    coros = [
        _execute_case(
            case_index=index,
            chat_messages=_build_chat_messages(site.messages, case.inputs),
            client=client,
            parameters=parameters,
        )
        for index, case in enumerate(dataset_cases)
    ]
    # asyncio.gather preserves order, so ``results[i]`` corresponds to
    # ``dataset_cases[i]`` — the per-case index in RunOutput is therefore
    # already correct without a re-sort.
    results = await asyncio.gather(*coros)

    outputs: list[RunOutput] = []
    responses: list[ChatResponse | None] = []
    total_input = 0
    total_output = 0
    total_cost = 0.0
    for run_output, chat_response in results:
        outputs.append(run_output)
        responses.append(chat_response)
        if chat_response is not None:
            total_input += chat_response.usage.input_tokens
            total_output += chat_response.usage.output_tokens
            total_cost += chat_response.cost_usd

    return PromptRunResult(
        outputs=outputs,
        responses=responses,
        metrics=PromptRunMetrics(
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=total_cost,
        ),
    )
