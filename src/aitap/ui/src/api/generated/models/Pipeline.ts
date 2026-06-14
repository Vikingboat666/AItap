/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PipelineEdge } from './PipelineEdge';
import type { PipelineNode } from './PipelineNode';
/**
 * A directed acyclic graph of LLM calls connected by data flow.
 */
export type Pipeline = {
    id: string;
    name: string;
    nodes: Array<PipelineNode>;
    edges: Array<PipelineEdge>;
    entry_points?: Array<string>;
    exit_points?: Array<string>;
};

