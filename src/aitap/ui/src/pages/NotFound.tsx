import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../components/primitives";

export function NotFound() {
  const { t } = useTranslation();
  return (
    <EmptyState
      title={t("notFound.title")}
      hint={
        <Link to="/" className="text-brand-600 hover:underline">
          {t("notFound.backToInventory")}
        </Link>
      }
    />
  );
}
