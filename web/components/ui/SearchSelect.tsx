// A searchable dropdown ("combobox"), like the industry and location pickers:
// type to filter, ↑/↓ to move, Enter to choose, Escape to close, ✕ to clear.
//
//   [ 11–50                              ✕ ]
//     11–50 employees        Small business
//     51–200 employees       Mid-size
//
// Used for the business size question and the consultant's "Working on"
// client picker. Only listed options can be chosen; clearing sets null.

"use client";

import { useId, useMemo, useRef, useState, type ReactNode } from "react";

export interface SearchOption {
  value: string;
  label: string;
  /** Second line (smaller, muted). */
  detail?: string;
  /** Extra words to match on, not shown. */
  keywords?: string;
  /** Its own layout in the list (instead of label + detail); the label is still what the box shows. */
  render?: ReactNode;
}

export default function SearchSelect(props: {
  options: SearchOption[];
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder: string;
  label: string; // for screen readers
  /** Tailwind classes for the focus color (default: the brand gold). */
  accent?: "brand" | "consultant";
}) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const chosen = props.options.find((o) => o.value === props.value) ?? null;

  const shown = useMemo(() => {
    const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return props.options;
    return props.options.filter((o) => {
      const hay = `${o.label} ${o.detail ?? ""} ${o.keywords ?? ""}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [props.options, text]);

  function pick(o: SearchOption) {
    props.onChange(o.value);
    setText("");
    setOpen(false);
  }

  const ring = props.accent === "consultant" ? "focus:border-consultant focus:ring-consultant/20" : "focus:border-brand focus:ring-brand/20";

  return (
    <div className="relative">
      <input
        ref={inputRef}
        role="combobox"
        aria-label={props.label}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && shown[active] ? `${listId}-${active}` : undefined}
        className={`w-full rounded-xl border border-line bg-surface px-3 py-2 pr-9 text-sm text-ink outline-none focus:ring-2 ${ring} ${
          chosen && !text ? "placeholder:text-ink" : ""
        }`}
        placeholder={chosen ? chosen.label : props.placeholder}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, shown.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && open && shown[active]) {
            e.preventDefault();
            pick(shown[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {(chosen || text) && (
        <button
          type="button"
          aria-label={`Clear ${props.label}`}
          onClick={() => {
            props.onChange(null);
            setText("");
            inputRef.current?.focus();
          }}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full px-2 text-ink-muted hover:text-ink"
        >
          ✕
        </button>
      )}
      {open && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-line bg-surface py-1 text-left shadow-[var(--shadow-card)]"
        >
          {shown.length === 0 && <li className="px-3 py-2 text-sm text-ink-muted">No matches</li>}
          {shown.map((o, i) => (
            <li
              key={o.value}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={o.value === props.value}
              onMouseDown={(e) => {
                e.preventDefault(); // keep focus so the click lands
                pick(o);
              }}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer px-3 py-2 text-sm normal-case tracking-normal ${i === active ? "bg-surface-2" : ""} ${
                o.value === props.value ? "font-semibold" : ""
              }`}
            >
              {o.render ?? (
                <>
                  <div className="text-ink">{o.label}</div>
                  {o.detail && <div className="text-xs font-normal text-ink-muted">{o.detail}</div>}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
