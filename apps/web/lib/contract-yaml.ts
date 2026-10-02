import type { ContractDefinition, ContractFieldRule } from "@/lib/types";

/* YAML's plain scalars are permissive enough to misread: `no` is false, `1.0` a number,
   `a: b` a mapping. Anything that could be read as something else is written as a JSON
   string, which YAML reads exactly. */
const PLAIN = /^[A-Za-z_][A-Za-z0-9_. /()'-]*$/;
const KEYWORDS = /^(true|false|yes|no|on|off|null|y|n|~)$/i;

function scalar(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  const text = String(value);
  if (PLAIN.test(text) && !KEYWORDS.test(text) && text.trim() === text) return text;
  return JSON.stringify(text);
}

function rule(spec: ContractFieldRule): string {
  const parts: string[] = [`type: ${spec.type ?? "any"}`];
  if (spec.enum?.length) parts.push(`enum: [${spec.enum.map(scalar).join(", ")}]`);
  if (spec.minimum !== undefined && spec.minimum !== null) parts.push(`minimum: ${spec.minimum}`);
  if (spec.maximum !== undefined && spec.maximum !== null) parts.push(`maximum: ${spec.maximum}`);
  if (spec.max_length) parts.push(`max_length: ${spec.max_length}`);
  // Always quoted: a regular expression is full of characters YAML would act on.
  if (spec.pattern) parts.push(`pattern: ${JSON.stringify(spec.pattern)}`);
  if (spec.description) parts.push(`description: ${scalar(spec.description)}`);
  return `{${parts.join(", ")}}`;
}

/** A contract as YAML — the form people keep next to their code. One direction only: the
 * server reads YAML, so what is shown is what it would read back. */
export function contractYaml(definition: ContractDefinition): string {
  const lines: string[] = [`event: ${definition.event_type ?? ""}`];
  if (definition.mode && definition.mode !== "warn") lines.push(`mode: ${definition.mode}`);
  if (definition.description) lines.push(`description: ${scalar(definition.description)}`);
  const fields = Object.entries(definition.fields ?? {});
  const required = [
    ...new Set([
      ...(definition.required ?? []),
      ...fields.filter(([, spec]) => spec.required).map(([path]) => path),
    ]),
  ];
  if (required.length) lines.push(`required: [${required.join(", ")}]`);
  // A required field the contract says nothing else about needs no line of its own.
  const described = fields.filter(
    ([path, spec]) =>
      !(
        required.includes(path) &&
        Object.keys(spec).every((key) => key === "type" || key === "required") &&
        (spec.type ?? "any") === "any"
      ),
  );
  if (described.length) {
    lines.push("fields:");
    for (const [path, spec] of described) lines.push(`  ${path}: ${rule(spec)}`);
  }
  if (definition.text_field) lines.push(`text_field: ${definition.text_field}`);
  if (definition.importance !== null && definition.importance !== undefined) {
    lines.push(`importance: ${definition.importance}`);
  }
  if (definition.allow_extra === false) lines.push("allow_extra: false");
  return `${lines.join("\n")}\n`;
}
