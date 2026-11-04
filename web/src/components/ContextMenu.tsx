// *right-clicks with a tiny paw* OwO web/src/components/ContextMenu.tsx: RIGHT-CLICK MENUS, like Proxmox! ^w^
// A tree row, a table row or a map node can open a menu of what you can do with it. Components call showMenu() from
// their onContextMenu; the ONE ContextMenuHost in the app shell draws the menu (and any dialog a menu entry opens).
// Shift + right-click still gives the browser's own menu *wags tail*
import { KeyboardEvent, MouseEvent, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
// ApiError for error dialogs; ui pieces: ErrorBox, the menu item types, MenuList (draws items) and Modal UwU
import { ApiError } from "../api";
import { ErrorBox, MenuAction, MenuItems, MenuList, Modal } from "./ui";

// Right-click menus, as in Proxmox: a row of the resource tree or of a table, or a node on a map, opens a menu of
// what can be done to it. A component calls showMenu from its onContextMenu; the one ContextMenuHost in the shell
// draws the menu and any dialog an entry opens. Shift + right-click still opens the browser's own menu.

// Menu: an optional title and the items. Request: a menu plus where to show it, whether it came from the keyboard,
// and the element it belongs to >w<
export interface Menu { title?: string; items: MenuItems }

interface Request extends Menu { x: number; y: number; keyboard: boolean; target: HTMLElement }

// showMenu: called from onContextMenu. Skips when Shift is held or there's nothing to show; otherwise stops the
// browser's menu and sends a "valor-menu" event with the details to the host OwO
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
// openDialog: show a dialog over the page (the host draws whatever `render` returns; `close` removes it) ^w^
export function openDialog(render: (close: () => void) => ReactNode) {
  window.dispatchEvent(new CustomEvent("valor-dialog", { detail: render }));
}

/** An action started from a menu has no page of its own to show an error on: it shows in a dialog. */
// showError: an error from a menu action, shown in a small dialog (there's no page to show it on) UwU
export function showError(e: ApiError) {
  openDialog((close) => (
    <Modal title="Error" onClose={close} footer={<button className="btn primary" onClick={close}>OK</button>}>
      <ErrorBox error={e} />
    </Modal>
  ));
}

/** The menu entry that keeps what the browser's own menu offered on a link. */
// newTab: the "Open in a new tab" entry (noopener: the new tab can't reach back into this one) >w<
export const newTab = (href: string): MenuAction =>
  ({ label: "Open in a new tab", icon: "link", onClick: () => window.open(`#/${href}`, "_blank", "noopener") });

// ContextMenuHost: lives once in the app shell and draws menus and dialogs OwO
export function ContextMenuHost({ page }: { page: string }) {
  const [menu, setMenu] = useState<Request | null>(null);
  const [dialog, setDialog] = useState<{ render: (close: () => void) => ReactNode } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Listen for "valor-menu" and "valor-dialog" events; close both whenever the page changes ^w^
  useEffect(() => {
    const onMenu = (e: Event) => setMenu((e as CustomEvent<Request>).detail);
    const onDialog = (e: Event) => setDialog({ render: (e as CustomEvent).detail });
    window.addEventListener("valor-menu", onMenu);
    window.addEventListener("valor-dialog", onDialog);
    return () => { window.removeEventListener("valor-menu", onMenu); window.removeEventListener("valor-dialog", onDialog); };
  }, []);
  useEffect(() => { setMenu(null); setDialog(null); }, [page]);

  // Open: mark the row it belongs to, keep the menu inside the window, close on any click, wheel or resize elsewhere.
  // After the menu renders (useLayoutEffect = before the browser paints): position it inside the window, focus it (or
  // its first item for keyboard users), mark the row it belongs to, and close it on clicks/wheel outside, resize or
  // leaving the window. The cleanup undoes all of that UwU
  useLayoutEffect(() => {
    const box = ref.current;
    if (!menu || !box) return;
    box.style.left = `${Math.max(4, Math.min(menu.x, window.innerWidth - box.offsetWidth - 4))}px`;
    box.style.top = `${menu.y + box.offsetHeight > window.innerHeight - 4 ? Math.max(4, menu.y - box.offsetHeight) : menu.y}px`;
    (menu.keyboard ? box.querySelector<HTMLElement>(".menu-item:not(:disabled)") ?? box : box).focus();
    menu.target.dataset.menuOpen = "";
    const close = () => setMenu(null);
    const outside = (e: Event) => !box.contains(e.target as Node) && close();
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

  // onKey: arrow keys move between items (wrapping around); Escape/Tab close the menu and give focus back >w<
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

  // Draw the menu (when there is one) and the dialog (when there is one) OwO
  return (
    <>
      {menu && (
        <div className="popover menu context-menu" role="menu" aria-label={menu.title} tabIndex={-1} ref={ref} onKeyDown={onKey}
          onContextMenu={(e) => e.preventDefault()}>
          {/* The menu's title, then its items; picking one closes the menu and runs the item */}
          {menu.title && <div className="menu-title">{menu.title}</div>}
          <MenuList items={menu.items} onPick={(item) => { setMenu(null); item.onClick(); }} />
        </div>
      )}
      {/* The dialog, if any, with a close function */}
      {dialog?.render(() => setDialog(null))}
    </>
  );
}
