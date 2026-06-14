/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CallParameters } from './CallParameters';
import type { DatasetCase } from './DatasetCase';
import type { Provider } from './Provider';
export type RunCreate = {
    target_kind: 'prompt' | 'pipeline';
    target_id: string;
    target_version: number;
    cases?: Array<DatasetCase>;
    dataset_id?: (string | null);
    provider: Provider;
    model: string;
    profile_id?: (string | null);
    parameters: CallParameters;
    pipeline_mode?: ('node' | 'segment' | 'end_to_end' | null);
    pipeline_node_id?: (string | null);
    pipeline_segment?: (Array<string> | null);
};

