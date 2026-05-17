/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CallParameters } from './CallParameters';
import type { DatasetCase } from './DatasetCase';
import type { Provider } from './Provider';
export type RunCreate = {
    cases?: Array<DatasetCase>;
    dataset_id?: (string | null);
    model: string;
    parameters: CallParameters;
    pipeline_segment?: (Array<string> | null);
    provider: Provider;
    target_id: string;
    target_kind: 'prompt' | 'pipeline';
    target_version: number;
};

