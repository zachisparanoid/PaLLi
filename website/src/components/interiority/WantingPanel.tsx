import type { WantingSection } from "@/lib/types";

const STATUS_TONE: Record<string, string> = {
  alive: "text-emerald-300/80",
  dormant: "text-white/40",
  done: "text-sky-300/70",
  released: "text-white/30",
};

export function WantingPanel({ section }: { section?: WantingSection }) {
  if (!section || !section.enabled) return null;
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <h3 className="text-sm font-medium text-white/70">Wanting</h3>
      {section.error ? (
        <p className="mt-2 text-xs text-amber-300/70">
          couldn&apos;t be read just now
        </p>
      ) : (
        <>
          <ul className="mt-2 space-y-1">
            {section.quests.map((q) => (
              <li key={q.id} className="text-xs text-white/60">
                <span className={STATUS_TONE[q.status] ?? "text-white/50"}>
                  [{q.status}]
                </span>{" "}
                {q.text}
                {q.progress.length > 0 ? (
                  <span className="text-white/35">
                    {" "}
                    — {q.progress[q.progress.length - 1].note}
                  </span>
                ) : null}
              </li>
            ))}
            {section.quests.length === 0 ? (
              <li className="text-xs text-white/35">her wishbook is empty</li>
            ) : null}
          </ul>
          {section.daydreams.length > 0 ? (
            <div className="mt-3 border-t border-white/5 pt-2">
              {section.daydreams.slice(0, 4).map((d) => (
                <p key={d.ts} className="text-xs italic text-white/40">
                  {d.text}
                </p>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
