"""Scanner data contract.

Contract version: 1 (2026-05-09)

These models flow from the scanner to the store, the playground runner,
the iteration loop, and the web UI. Treat them as a public API:

- Adding a new optional field is backward compatible.
- Renaming, removing, or retyping a field is a breaking change and requires
  a CONTRACTS.md change-protocol PR.

Example consumer (downstream worktrees should mirror this shape):

    from aitap.scanner.engine import scan_project
    result: ScanResult = scan_project(project_root)
    for site in result.prompts:
        print(site.id, site.template_text[:80])
    for pipeline in result.pipelines:
        print(pipeline.id, [n.prompt_id for n in pipeline.nodes])
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Provider(str, Enum):
    """LLM provider identified by static analysis or env inspection."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    LANGCHAIN = "langchain"
    LLAMAINDEX = "llamaindex"
    DASHSCOPE = "dashscope"
    UNKNOWN = "unknown"


class Role(str, Enum):
    """Message role inside a chat-style prompt."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TemplateKind(str, Enum):
    """How the prompt text is constructed in source."""

    LITERAL = "literal"          # plain string
    FSTRING = "fstring"          # Python f-string with interpolation
    JINJA2 = "jinja2"            # jinja2 template
    CONCAT = "concat"            # string concatenation across multiple lines
    UNRESOLVED = "unresolved"    # too complex for L1 — needs L2


class Confidence(str, Enum):
    """How sure the scanner is that this site is a real LLM call."""

    HIGH = "high"        # known SDK signature match
    MEDIUM = "medium"    # heuristic match (custom wrapper suspected)
    LOW = "low"          # weak signal, likely needs L2 confirmation


class CodeLocation(BaseModel):
    """Where in the source tree a finding lives. Paths are project-relative POSIX."""

    model_config = ConfigDict(frozen=True)

    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    col_start: int | None = None
    col_end: int | None = None


class TemplateVariable(BaseModel):
    """A `{var}` slot inside a template that gets filled at call time."""

    model_config = ConfigDict(frozen=True)

    name: str
    inferred_type: str | None = None  # populated by L2 (e.g., "email body", "user query")


class Message(BaseModel):
    """A single role+content pair inside a chat-style prompt."""

    model_config = ConfigDict(frozen=True)

    role: Role
    template_text: str
    template_kind: TemplateKind = TemplateKind.LITERAL
    variables: list[TemplateVariable] = Field(default_factory=list)


class CallParameters(BaseModel):
    """Model-call parameters captured statically from the call site."""

    model_config = ConfigDict(frozen=True)

    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    response_format: str | None = None  # "text" | "json" | "json_schema" | etc.
    extra: dict[str, str] = Field(default_factory=dict)


class PromptSite(BaseModel):
    """One identified LLM call point in source code."""

    model_config = ConfigDict(frozen=True)

    id: str  # stable hash of file + line + template — used as primary key downstream
    name: str  # human-friendly slug derived from function/var context (e.g., "summarize_email")
    provider: Provider
    location: CodeLocation
    messages: list[Message]
    parameters: CallParameters = Field(default_factory=CallParameters)
    purpose: str | None = None  # filled by L2 (e.g., "summarize incoming customer emails")
    confidence: Confidence = Confidence.HIGH
    tags: list[str] = Field(default_factory=list)


class EdgeKind(str, Enum):
    """What kind of data dependency this edge represents."""

    VARIABLE = "variable"      # x = call_a(); call_b(x)
    LANGCHAIN_PIPE = "lc_pipe"  # prompt | model | parser
    LLAMAINDEX = "llamaindex"   # query engine chain
    FUNCTION = "function"       # f() returns; g(f())
    UNRESOLVED = "unresolved"   # detected but not confirmed (dashed in UI)


class PipelineNode(BaseModel):
    """A node in the pipeline DAG, referencing a PromptSite by id."""

    model_config = ConfigDict(frozen=True)

    prompt_id: str
    label: str | None = None  # optional display override


class PipelineEdge(BaseModel):
    """A directed edge: source's output is fed to target."""

    model_config = ConfigDict(frozen=True)

    source: str  # prompt_id of upstream
    target: str  # prompt_id of downstream
    kind: EdgeKind
    via: str | None = None  # variable name or operator that carries the data
    confidence: Confidence = Confidence.HIGH


class Pipeline(BaseModel):
    """A directed acyclic graph of LLM calls connected by data flow."""

    model_config = ConfigDict(frozen=True)

    id: str  # stable hash of node ids
    name: str
    nodes: list[PipelineNode]
    edges: list[PipelineEdge]
    entry_points: list[str] = Field(default_factory=list)  # prompt_ids with no incoming edge
    exit_points: list[str] = Field(default_factory=list)   # prompt_ids with no outgoing edge


class ProviderEvidence(BaseModel):
    """What the env scan turned up about configured providers."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    source: Literal[".env", "config", "code"]
    location: CodeLocation
    key_var_name: str  # e.g., "ANTHROPIC_API_KEY" — value is NEVER read or stored


class ScanWarning(BaseModel):
    """Non-fatal issue surfaced during scanning."""

    model_config = ConfigDict(frozen=True)

    code: str  # stable identifier, e.g., "W001-template-unresolved"
    message: str
    location: CodeLocation | None = None


class ScanResult(BaseModel):
    """Top-level output of a scan run. Serialized to JSON for CLI/API."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    project_root: str
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    git_commit: str | None = None
    files_scanned: int
    prompts: list[PromptSite]
    pipelines: list[Pipeline]
    providers_detected: list[ProviderEvidence]
    warnings: list[ScanWarning] = Field(default_factory=list)
    l2_used: bool = False
