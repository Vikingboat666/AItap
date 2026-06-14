/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Confidence } from './Confidence';
import type { EdgeKind } from './EdgeKind';
/**
 * A directed edge: source's output is fed to target.
 */
export type PipelineEdge = {
    source: string;
    target: string;
    kind: EdgeKind;
    via?: (string | null);
    confidence?: Confidence;
};

