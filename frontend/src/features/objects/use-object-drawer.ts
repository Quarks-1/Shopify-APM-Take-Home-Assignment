import { useCallback } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";

export interface ObjectDrawerApi {
  objectId: string | null;
  isNew: boolean;
  open(id: string, opts?: { isNew?: boolean }): void;
  close(): void;
}

/** Drawer state lives in the URL: `?object=<id>` plus `new=1` for create mode. */
export function useObjectDrawer(): ObjectDrawerApi {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();

  const objectId = params.get("object");
  const isNew = params.get("new") === "1";
  const pushed = (location.state as { objectDrawerPushed?: boolean } | null)?.objectDrawerPushed === true;

  const open = useCallback(
    (id: string, opts?: { isNew?: boolean }) => {
      const next = new URLSearchParams(params);
      next.set("object", id);
      if (opts?.isNew) next.set("new", "1");
      else next.delete("new");
      navigate({ search: next.toString() }, { state: { objectDrawerPushed: true } });
    },
    [params, navigate],
  );

  const close = useCallback(() => {
    if (pushed) {
      navigate(-1);
      return;
    }
    const next = new URLSearchParams(params);
    next.delete("object");
    next.delete("new");
    setParams(next, { replace: true });
  }, [pushed, params, navigate, setParams]);

  return { objectId, isNew, open, close };
}
