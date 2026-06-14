/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where in the source tree a finding lives. Paths are project-relative POSIX.
 */
export type CodeLocation = {
    file: string;
    line_start: number;
    line_end: number;
    col_start?: (number | null);
    col_end?: (number | null);
};

