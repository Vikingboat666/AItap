import { createBrowserRouter } from "react-router-dom";

import App from "./App";
import { Inventory } from "./pages/Inventory";
import { PromptDetail } from "./pages/PromptDetail";
import { PipelineDetail } from "./pages/PipelineDetail";
import { Playground } from "./pages/Playground";
import { History } from "./pages/History";
import { HistoryLanding } from "./pages/HistoryLanding";
import { Audit } from "./pages/Audit";
import { Settings } from "./pages/Settings";
import { NotFound } from "./pages/NotFound";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Inventory /> },
      { path: "prompts/:id", element: <PromptDetail /> },
      { path: "pipelines/:id", element: <PipelineDetail /> },
      { path: "playground", element: <Playground /> },
      { path: "playground/:targetKind/:targetId", element: <Playground /> },
      { path: "history", element: <HistoryLanding /> },
      { path: "history/:promptId", element: <History /> },
      { path: "audit", element: <Audit /> },
      { path: "settings", element: <Settings /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
