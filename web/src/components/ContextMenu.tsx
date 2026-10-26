import { KeyboardEvent, MouseEvent, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { ErrorBox, MenuAction, MenuItems, MenuList, Modal } from "./ui";

// Right-click menus, as in Proxmox: a row of the resource tree or of a table, or a node on a map, opens a menu of
// what can be done to it. A component calls showMenu from its onContextMenu; the one ContextMenuHost in the shell
// draws the menu and any dialog an entry opens. Shift + right-click still opens the browser's own menu.

export interface Menu { title?: string; items: MenuItems }

interface Request extends Menu { x: number; y: number; keyboard: boolean; target: HTMLElement }

export function showMenu(e: MouseEvent, menu: Menu) {
  if (e.shiftKey || !menu.items.some(Boolean)) return;
  e.preventDefault();
  e.stopPropagation();
  const target = e.currentTarget as HTMLElement;
  // The context-menu key (or Shift+F10) gives no pointer position: open below the element instead.
  const keyboard = e.clientX === 0 && e.clientY === 0;
  const box = target.getBoundingClientRect();
  const detail: Request = { ...menu, target, keyboard, x: keyboard ? box.left : e.clientX, y: keyboard ? box.bottom : e.clientY };
  window.dispatchEvent(new CustomEvent("valor-menu", { detail }));
}

/** Opens a dialog over the page, e.g. a range's Login from the tree; `close` removes it. */
export function openDialog(render: (close: () => void) => ReactNode) {
  window.dispatchEvent(new CustomEvent("valor-dialog", { detail: render }));
}

/** An action started from a menu has no page of its own to show an error on: it shows in a dialog. */
export function showError(e: ApiError) {
  openDialog((close) => (
    <Modal title="Error" onClose={close} footer={<button className="btn primary" onClick={close}>OK</button>}>
      <ErrorBox error={e} />
    </Modal>
  ));
}

/** The menu entry that keeps what the browser's own menu offered on a link. */
export const newTab = (href: string): MenuAction =>
  ({ label: "Open in a new tab", icon: "link", onClick: () => window.open(`#/${href}`, "_blank", "noopener") });

export function ContextMenuHost({ page }: { page: string }) {
  const [menu, setMenu] = useState<Request | null>(null);
  const [dialog, setDialog] = useState<{ render: (close: () => void) => ReactNode } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onMenu = (e: Event) => setMenu((e as CustomEvent<Request>).detail);
    const onDialog = (e: Event) => setDialog({ render: (e as CustomEvent).detail });
    window.addEventListener("valor-menu", onMenu);
    window.addEventListener("valor-dialog", onDialog);
    return () => { window.removeEventListener("valor-menu", onMenu); window.removeEventListener("valor-dialog", onDialog); };
  }, []);
  useEffect(() => { setMenu(null); setDialog(null); }, [page]);

  // Open: mark the row it belongs to, keep the menu inside the window, close on any click, wheel or resize elsewhere.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!menu || !el) return;
    el.style.left = `${Math.max(4, Math.min(menu.x, window.innerWidth - el.offsetWidth - 4))}px`;
    el.style.top = `${menu.y + el.offsetHeight > window.innerHeight - 4 ? Math.max(4, menu.y - el.offsetHeight) : menu.y}px`;
    (menu.keyboard ? el.querySelector<HTMLElement>(".menu-item:not(:disabled)") ?? el : el).focus();
    menu.target.dataset.menuOpen = "";
    const close = () => setMenu(null);
    const outside = (e: Event) => !el.contains(e.target as Node) && close();
    document.addEventListener("mousedown", outside);
    document.addEventListener("wheel", outside);
    window.addEventListener("resize", close);
    window.addEventListener("blur", close);
    return () => {
      delete menu.target.dataset.menuOpen;
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("wheel", outside);
      window.removeEventListener("resize", close);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  const onKey = (e: KeyboardEvent) => {
    const items = [...ref.current!.querySelectorAll<HTMLElement>(".menu-item:not(:disabled)")];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const step = e.key === "ArrowDown" ? 1 : items.length - 1;
      items[at < 0 ? (step === 1 ? 0 : items.length - 1) : (at + step) % items.length]?.focus();
    } else if (e.key === "Escape" || e.key === "Tab") {
      setMenu(null);
      menu?.target.focus();
    } else return;
    e.preventDefault();
    e.stopPropagation();          // Escape closes the menu only, not a dialog under it
  };

  return (
    <>
      {menu && (
        <div className="popover menu context-menu" role="menu" aria-label={menu.title} tabIndex={-1} ref={ref} onKeyDown={onKey}
          onContextMenu={(e) => e.preventDefault()}>
          {menu.title && <div className="menu-title">{menu.title}</div>}
          <MenuList items={menu.items} onPick={(i) => { setMenu(null); i.onClick(); }} />
        </div>
      )}
      {dialog?.render(() => setDialog(null))}
    </>
  );
}
