/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Role } from './Role';
import type { TemplateKind } from './TemplateKind';
import type { TemplateVariable } from './TemplateVariable';
/**
 * A single role+content pair inside a chat-style prompt.
 */
export type Message = {
    role: Role;
    template_kind?: TemplateKind;
    template_text: string;
    variables?: Array<TemplateVariable>;
};

