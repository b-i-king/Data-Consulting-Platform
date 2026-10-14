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
  const excluded = props.options.excluded_formats;
  const formats = props.options.file_formats;
  const recommended = formats.filter((f) => f.recommended);
  // Group the other formats under headings, keeping the backend's order.
  const groups = formats
    .filter((f) => !f.recommended)
    .reduce<Record<string, typeof formats>>((acc, f) => ({ ...acc, [f.group]: [...(acc[f.group] ?? []), f] }), {});

  /** Validate and merge newly picked files with the ones already chosen. */
  function addFiles(list: FileList | null) {
    if (!list) return;
    setError(null);
    const next = [...props.files];
    for (const f of Array.from(list)) {
      const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
      if (excluded[ext]) {
        setError(`"${f.name}": ${excluded[ext]}`);
        continue;
      }
      if (!exts.includes(ext)) {
        setError(`"${f.name}" isn't a supported file type. CSV or Excel work best — see all supported formats below.`);
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
      // New files start with no per-column choices (they're made on the next step).
      props.onAnalyzed(await analyzeFiles({ files: props.files, strategy: props.strategy, columnActions: {}, columnNames: {} }));
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
        className={`cursor-pointer rounded-2xl border-2 border-dashed p-10 text-center transition-colors ${
          dragging ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand"
        }`}
      >
        <p className="font-medium">Drag files here, or click to browse</p>
        <p className="mt-1 text-sm text-ink-muted">
          Up to {max_upload_mb} MB each. The first row should hold your column names.
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

      {/* Supported formats: CSV and Excel first, everything else tucked away. */}
      <div className="mt-3 space-y-2 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-ink-muted">Recommended:</span>
          {recommended.map((f) => (
            <span
              key={f.id}
              title={f.note}
              className="rounded-full border border-brand/40 bg-brand-soft px-2.5 py-0.5 font-mono text-xs"
            >
              {f.label} <span className="text-ink-muted">{f.extensions.join(" ")}</span>
            </span>
          ))}
        </div>
        <details className="group rounded-xl border border-line bg-surface px-4 py-2">
          <summary className="cursor-pointer text-ink-muted hover:text-ink">
            See all {formats.length} supported formats (including .zip and .gz archives)
          </summary>
          <div className="mt-3 grid gap-4 pb-2 sm:grid-cols-2">
            {Object.entries(groups).map(([group, list]) => (
              <div key={group}>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">{group}</h3>
                <ul className="space-y-1">
                  {list.map((f) => (
                    <li key={f.id}>
                      <span className="font-medium">{f.label}</span>{" "}
                      <span className="font-mono text-xs text-ink-muted">{f.extensions.join(" ")}</span>
                      <div className="text-xs text-ink-muted">{f.note}</div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-muted">Compressed</h3>
              <p className="text-xs text-ink-muted">
                <span className="font-mono">.zip .gz .bz2 .xz</span> — e.g. <span className="font-mono">sales.csv.gz</span>,
                or a .zip holding several files.
              </p>
            </div>
          </div>
          <p className="border-t border-line pt-2 pb-1 text-xs text-ink-muted">
            Not accepted for your security: pickle (.pkl) and HDF5 (.h5) files, which can run hidden code when opened.
          </p>
        </details>
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
