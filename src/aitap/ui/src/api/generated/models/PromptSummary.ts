/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Confidence } from './Confidence';
import type { Provider } from './Provider';
export type PromptSummary = {
    id: string;
    name: string;
    provider: Provider;
    file: string;
    line_start: number;
    purpose: (string | null);
    confidence: Confidence;
    latest_version: number;
};

