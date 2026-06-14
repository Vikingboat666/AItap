/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CallParameters } from './CallParameters';
import type { Message } from './Message';
export type PromptVersionCreate = {
    messages: Array<Message>;
    parameters: CallParameters;
    note?: (string | null);
    parent_version?: (number | null);
};

