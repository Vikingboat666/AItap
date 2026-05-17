/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Confidence } from './Confidence';
import type { Provider } from './Provider';
export type PromptSummary = {
    confidence: Confidence;
    file: string;
    id: string;
    latest_version: number;
    line_start: number;
    name: string;
    provider: Provider;
    purpose: (string | null);
};

