// Step 2 — upload one or more flat files.
// Files are checked in the browser first (type, size, count) for instant
// feedback; the backend checks again because browser checks can be bypassed.

"use client";

import { useRef, useState, type DragEvent } from "react";
import { analyzeFiles } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import type { AnalyzeResponse, IntakeOptions, MissingStrategy } from "@/lib/types";
import { Button, Card, ChoiceCard, ErrorBox, NavRow, StepHeader } from "./ui";

export default function StepUpload(props: {
  files: File[];
  strategy: MissingStrategy;
  options: IntakeOptions;
  onFilesChange: (files: File[]) => void;
  onStrategyChange: (s: MissingStrategy) => void;
  onAnalyzed: (r: AnalyzeResponse) => void;
  onBack: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { max_upload_mb, max_files_per_upload } = props.options.limits;
  const exts = props.options.supported_extensions;

  /** Validate and merge newly picked files with the ones already chosen. */
  function addFiles(list: FileList | null) {
    if (!list) return;
    setError(null);
    const next = [...props.files];
    for (const f of Array.from(list)) {
      const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
      if (!exts.includes(ext)) {
        setError(`"${f.name}" isn't a supported file type (${exts.join(", ")}).`);
        continue;
      }
      if (f.size > max_upload_mb * 1024 * 1024) {
        setError(`"${f.name}" is larger than ${max_upload_mb} MB.`);
        continue;
      }
      if (!next.some((x) => x.name === f.name)) next.push(f);
    }
    if (next.length > max_files_per_upload) {
      setError(`Please choose ${max_files_per_upload} files or fewer.`);
      return;
    }
    props.onFilesChange(next);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  }

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      props.onAnalyzed(await analyzeFiles(props.files, props.strategy));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong while reading your files.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <StepHeader
        title="Upload your files"
        subtitle="One file goes straight to cleaning. Several files? We'll figure out how they fit together first."
      />

      {/* Drop zone — also clickable for people who prefer a file picker. */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
        className={`cursor-pointer rounded-lg border-2 border-dashed p-10 text-center transition-colors ${
          dragging ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
        }`}
      >
        <p className="font-medium">Drag files here, or click to browse</p>
        <p className="mt-1 text-sm text-ink-muted">
          {exts.join(" · ")} — up to {max_upload_mb} MB each. The first row should hold your column names.
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={exts.join(",")}
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = ""; // allow picking the same file again after removing it
          }}
        />
      </div>

      {props.files.length > 0 && (
        <Card className="mt-4">
          <h2 className="mb-2 text-sm font-medium">
            {props.files.length} file{props.files.length > 1 ? "s" : ""} selected
          </h2>
          <ul className="divide-y divide-line text-sm">
            {props.files.map((f) => (
              <li key={f.name} className="flex items-center justify-between py-2">
                <span className="truncate">{f.name}</span>
                <span className="flex items-center gap-3 text-ink-muted">
                  {fmtInt(Math.ceil(f.size / 1024))} KB
                  <button
                    type="button"
                    className="text-bad hover:underline"
                    onClick={() => props.onFilesChange(props.files.filter((x) => x !== f))}
                  >
                    Remove
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="mt-6">
        <h2 className="mb-2 text-sm font-medium">What should we do with rows that have missing values?</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <ChoiceCard
            label="Keep them (recommended)"
            description="Keep every row and get a report of where the gaps are. Nothing is lost."
            selected={props.strategy === "keep"}
            onClick={() => props.onStrategyChange("keep")}
          />
          <ChoiceCard
            label="Remove them"
            description="Drop any row with a missing value. Handy for some analyses, but can remove a lot of data."
            selected={props.strategy === "drop_any"}
            onClick={() => props.onStrategyChange("drop_any")}
          />
        </div>
      </div>

      <ErrorBox message={error} />

      <NavRow onBack={props.onBack}>
        <Button onClick={analyze} disabled={props.files.length === 0 || busy}>
          {busy ? "Cleaning your data…" : "Clean my data"}
        </Button>
      </NavRow>
    </section>
  );
}
