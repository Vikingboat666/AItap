/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CallParameters } from './CallParameters';
import type { CodeLocation } from './CodeLocation';
import type { Confidence } from './Confidence';
import type { Message } from './Message';
import type { Provider } from './Provider';
/**
 * One identified LLM call point in source code.
 */
export type PromptSite = {
    id: string;
    name: string;
    provider: Provider;
    location: CodeLocation;
    messages: Array<Message>;
    parameters?: CallParameters;
    purpose?: (string | null);
    confidence?: Confidence;
    tags?: Array<string>;
};

