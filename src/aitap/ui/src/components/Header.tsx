import { useLocation } from "react-router-dom";

const titles: Record<string, string> = {
  "/": "Inventory",
  "/playground": "Playground",
  "/history": "History",
  "/audit": "Audit",
};

function deriveTitle(pathname: string): string {
  if (titles[pathname]) return titles[pathname];
  if (pathname.startsWith("/prompts/")) return "Prompt detail";
  if (pathname.startsWith("/pipelines/")) return "Pipeline detail";
  if (pathname.startsWith("/playground")) return "Playground";
  if (pathname.startsWith("/history")) return "History";
  return "aitap";
}

export function Header() {
  const { pathname } = useLocation();
  return (
    <header className="flex h-14 items-center justify-between border-b border-ink-200 bg-white px-6">
      <h1 className="text-base font-medium text-ink-800">
        {deriveTitle(pathname)}
      </h1>
      <div className="flex items-center gap-3 text-xs text-ink-500">
        <span className="rounded-full bg-ink-100 px-2 py-0.5 font-mono text-[11px]">
          mock
        </span>
        <span>aitap@dev</span>
      </div>
    </header>
  );
}
