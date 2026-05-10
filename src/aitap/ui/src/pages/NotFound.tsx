import { Link } from "react-router-dom";
import { EmptyState } from "../components/primitives";

export function NotFound() {
  return (
    <EmptyState
      title="page not found"
      hint={
        <Link to="/" className="text-brand-600 hover:underline">
          back to inventory
        </Link>
      }
    />
  );
}
