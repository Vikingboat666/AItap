/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CallParameters } from './CallParameters';
import type { Message } from './Message';
export type PromptVersionCreate = {
    messages: Array<Message>;
    note?: (string | null);
    parameters: CallParameters;
    parent_version?: (number | null);
};

