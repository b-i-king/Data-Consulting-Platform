// Location picker for the signup form — type a city, pick it from the list
// (like Greenhouse's "Location" field).
//
//   [ atl                               ]
//     Atlanta, Georgia, United States
//     Atlixco, Mexico
//     Atlantic City, New Jersey, United States
//
// Suggestions come from GET /api/locations (api/services/locations.py), which
// searches a built-in GeoNames city list — no outside service. Type "paris, tx"
// to narrow by state or country. Keyboard: ↑/↓ to move, Enter to choose,
// Escape to close. Only a city picked from the list counts; free text is
// never saved, so every saved location is a real place.

"use client";

import { useEffect, useId, useRef, useState } from "react";
import { searchLocations } from "@/lib/api";
import type { Location } from "@/lib/types";

const inputCls =
  "w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

export default function LocationPicker(props: {
  value: Location | null;
  onChange: (v: Location | null) => void;
}) {
  const [text, setText] = useState("");
  const [options, setOptions] = useState<Location[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [noMatch, setNoMatch] = useState(false);   // the last search came back empty
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  // Search 200 ms after typing stops, so we don't send a request per keystroke.
  useEffect(() => {
    if (text.trim().length < 2) {
      setOptions([]);
      setNoMatch(false);
      return;
    }
    let cancelled = false;
    const t = setTimeout(() => {
      searchLocations(text)
        .then((r) => {
          if (!cancelled) {
            setOptions(r);
            setActive(0);
            setOpen(true);
            setNoMatch(r.length === 0);
          }
        })
        .catch(() => !cancelled && setOptions([]));
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [text]);

  function choose(loc: Location) {
    props.onChange(loc);
    setText("");
    setOptions([]);
    setOpen(false);
  }

  // Once chosen, show the city as a chip with a "Change" button.
  if (props.value) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-sm">
        <span>
          <span aria-hidden>📍 </span>
          {props.value.label}
        </span>
        <button
          type="button"
          className="text-brand-ink hover:underline"
          onClick={() => {
            props.onChange(null);
            setTimeout(() => inputRef.current?.focus(), 0);
          }}
        >
          Change
        </button>
      </div>
    );
  }

  return (
    <div className="relative">
      <input
        ref={inputRef}
        role="combobox"
        aria-expanded={open && options.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
        className={inputCls}
        placeholder="Start typing your city, e.g. Atlanta"
        value={text}
        autoComplete="off"
        onChange={(e) => setText(e.target.value)}
        onFocus={() => options.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}   // let a click on an option land first
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, options.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && open && options[active]) {
            e.preventDefault();   // choose the city instead of submitting the form
            choose(options[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {open && options.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-xl border border-line bg-surface py-1 shadow-[var(--shadow-card)]"
        >
          {options.map((o, i) => (
            // Keyboard users choose with the arrow keys + Enter in the input itself (the
            // ARIA combobox pattern, aria-activedescendant), so the options need no key handler.
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={o.id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}   // keep focus in the input
              onClick={() => choose(o)}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer px-3 py-1.5 text-sm ${i === active ? "bg-brand-soft" : ""}`}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
      {noMatch && text.trim().length >= 2 && (
        <p className="mt-1 text-xs text-ink-muted">
          No match yet. Try the nearest larger city, or add the state or country after a comma (e.g. “Paris, TX”).
        </p>
      )}
    </div>
  );
}
