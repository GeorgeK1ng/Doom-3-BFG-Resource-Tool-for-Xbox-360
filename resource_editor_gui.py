#!/usr/bin/env python3
"""
DOOM 3 BFG Resource Editor
==========================

A standalone Tkinter GUI for browsing and editing the contents of DOOM 3 BFG
Edition ``.resources`` container files (PC and Xbox 360 builds).

Features
--------
* Open any ``.resources`` archive and browse all contained files.
* Live filter / search by filename.
* Built-in text editor for readable entries (.script, .def, .gui, .mtr,
  .sndshd, .skin, .fx, .pda, etc.) with edit-in-place + save back into the
  archive.
* Hex-ish preview for binary entries.
* Extract a single entry, or extract everything, to disk.
* Replace any entry's bytes from an external file (e.g. an edited blob).
* Replace and add a whole folder tree in one operation.
* Add new files and delete existing ones.
* Save / Save As, rebuilding the archive with correct offsets and the
  original platform's byte order (verified byte-identical round-trip).

Requires only the Python standard library (Tkinter ships with the official
Windows / macOS Python installers).

Usage
-----
    python resource_editor_gui.py [optional_path_to.resources]
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Allow running from the same folder as the engine module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resource_archive import ResourceArchive, ResourceEntry  # noqa: E402


APP_TITLE = "DOOM 3 BFG Resource Editor"
PREVIEW_BINARY_LIMIT = 64 * 1024  # bytes shown in the hex view


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    return f"{n/1024/1024:.2f} MB"


class ResourceEditorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.archive: ResourceArchive | None = None
        self.current_entry: ResourceEntry | None = None
        self.dirty = False              # unsaved archive-level changes
        self.editor_dirty = False       # unsaved edits in the text pane

        root.title(APP_TITLE)
        root.geometry("1100x680")
        root.minsize(820, 500)

        self._build_menu()
        self._build_body()
        self._build_statusbar()
        self._update_title()

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------- UI build
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="Open…", accelerator="Ctrl+O",
                          command=self.open_archive)
        filem.add_command(label="Save", accelerator="Ctrl+S",
                          command=self.save_archive)
        filem.add_command(label="Save As…", command=self.save_archive_as)
        filem.add_separator()
        filem.add_command(label="Extract Selected…",
                          command=self.extract_selected)
        filem.add_command(label="Extract All…", command=self.extract_all)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=filem)

        editm = tk.Menu(menubar, tearoff=0)
        editm.add_command(label="Replace Selected From File…",
                          command=self.replace_from_file)
        editm.add_command(label="Replace/Add Files From Folder…",
                          command=self.replace_add_from_folder)
        editm.add_separator()
        editm.add_command(label="Add File…", command=self.add_file)
        editm.add_command(label="Delete Selected", command=self.delete_selected)
        menubar.add_cascade(label="Edit", menu=editm)

        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpm)

        self.root.config(menu=menubar)
        self.root.bind_all("<Control-o>", lambda e: self.open_archive())
        self.root.bind_all("<Control-s>", lambda e: self.save_archive())

    def _build_body(self) -> None:
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))

        # ---- left: search + file list ----
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        search_row = ttk.Frame(left)
        search_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search_row, text="Filter:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        ent = ttk.Entry(search_row, textvariable=self.search_var)
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        cols = ("name", "size")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="browse")
        self.tree.heading("name", text="File")
        self.tree.heading("size", text="Size")
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("size", width=80, anchor="e")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_entry)

        # ---- right: viewer / editor ----
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        info = ttk.Frame(right)
        info.pack(fill=tk.X)
        self.entry_label = ttk.Label(info, text="No file selected",
                                     font=("TkDefaultFont", 10, "bold"))
        self.entry_label.pack(side=tk.LEFT, anchor="w")

        btns = ttk.Frame(right)
        btns.pack(fill=tk.X, pady=4)
        self.save_edit_btn = ttk.Button(btns, text="Save Edit Into Archive",
                                        command=self.commit_text_edit,
                                        state=tk.DISABLED)
        self.save_edit_btn.pack(side=tk.LEFT)
        self.revert_btn = ttk.Button(btns, text="Revert",
                                     command=self.revert_text_edit,
                                     state=tk.DISABLED)
        self.revert_btn.pack(side=tk.LEFT, padx=(6, 0))

        text_frame = ttk.Frame(right)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(text_frame, wrap="none", undo=True,
                            font=("Consolas", 10))
        ysb = ttk.Scrollbar(text_frame, orient="vertical",
                            command=self.text.yview)
        xsb = ttk.Scrollbar(text_frame, orient="horizontal",
                            command=self.text.xview)
        self.text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        self.text.bind("<<Modified>>", self.on_text_modified)
        self._set_editor_enabled(False)

    def _build_statusbar(self) -> None:
        self.status = tk.StringVar(value="Open a .resources file to begin.")
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(bar, textvariable=self.status, anchor="w",
                  relief="sunken").pack(fill=tk.X, padx=4, pady=2)

    # ------------------------------------------------------------- helpers
    def _update_title(self) -> None:
        name = os.path.basename(self.archive.path) if self.archive and \
            self.archive.path else "—"
        mark = "*" if self.dirty else ""
        self.root.title(f"{APP_TITLE} — {name}{mark}")

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.text.configure(state=tk.NORMAL if enabled else tk.DISABLED)

    def set_status(self, msg: str) -> None:
        self.status.set(msg)

    # ------------------------------------------------------------- open/save
    def open_archive(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open .resources archive",
            filetypes=[("DOOM 3 BFG resources", "*.resources"),
                       ("All files", "*.*")])
        if not path:
            return
        self._load_path(path)

    def _load_path(self, path: str) -> None:
        try:
            self.archive = ResourceArchive.load(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open failed", str(exc))
            return
        self.dirty = False
        self.editor_dirty = False
        self.current_entry = None
        self.refresh_list()
        self._clear_editor()
        self._update_title()
        endian = ("PC / big-endian" if self.archive.int_endian == ">"
                  and self.archive.namelen_endian == ">"
                  else "Xbox 360 (mixed-endian)" )
        self.set_status(
            f"Loaded {len(self.archive.entries)} files — "
            f"{endian} layout — {os.path.basename(path)}")

    def save_archive(self) -> None:
        if not self.archive:
            return
        if self.archive.path is None:
            self.save_archive_as()
            return
        self._do_save(self.archive.path)

    def save_archive_as(self) -> None:
        if not self.archive:
            return
        path = filedialog.asksaveasfilename(
            title="Save .resources archive",
            defaultextension=".resources",
            filetypes=[("DOOM 3 BFG resources", "*.resources"),
                       ("All files", "*.*")])
        if not path:
            return
        self._do_save(path)

    def _do_save(self, path: str) -> None:
        try:
            self.archive.save(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save failed", str(exc))
            return
        self.dirty = False
        self._update_title()
        self.set_status(f"Saved {len(self.archive.entries)} files to "
                        f"{os.path.basename(path)}")

    # ------------------------------------------------------------- list view
    def refresh_list(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.archive:
            return
        flt = self.search_var.get().strip().lower()
        shown = 0
        for e in self.archive.entries:
            if flt and flt not in e.name.lower():
                continue
            self.tree.insert("", tk.END, iid=e.name,
                             values=(e.name, fmt_size(e.length)))
            shown += 1
        total = len(self.archive.entries)
        if flt:
            self.set_status(f"Showing {shown} / {total} files matching "
                            f"'{flt}'.")

    def on_select_entry(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        if self.editor_dirty and not self._confirm_discard_edit():
            return
        name = sel[0]
        entry = self.archive.find(name) if self.archive else None
        if entry is None:
            return
        self.current_entry = entry
        self._show_entry(entry)

    # ------------------------------------------------------------- viewer
    def _show_entry(self, entry: ResourceEntry) -> None:
        self.entry_label.config(
            text=f"{entry.name}    ({fmt_size(entry.length)})")
        data = entry.data or b""
        if entry.is_probably_text():
            self._show_text(data)
        else:
            self._show_binary(data)

    def _show_text(self, data: bytes) -> None:
        self._set_editor_enabled(True)
        self.text.delete("1.0", tk.END)
        try:
            self.text.insert("1.0", data.decode("latin1"))
        except Exception:  # noqa: BLE001
            self.text.insert("1.0", data.decode("utf-8", "replace"))
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.editor_dirty = False
        self.save_edit_btn.config(state=tk.DISABLED)
        self.revert_btn.config(state=tk.DISABLED)
        self._editable = True

    def _show_binary(self, data: bytes) -> None:
        self._set_editor_enabled(True)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", self._hexdump(data[:PREVIEW_BINARY_LIMIT]))
        if len(data) > PREVIEW_BINARY_LIMIT:
            self.text.insert(tk.END,
                             f"\n… ({len(data) - PREVIEW_BINARY_LIMIT} more "
                             "bytes not shown) …\n")
        self.text.edit_modified(False)
        self._set_editor_enabled(False)  # read-only for binary
        self.editor_dirty = False
        self.save_edit_btn.config(state=tk.DISABLED)
        self.revert_btn.config(state=tk.DISABLED)
        self._editable = False

    @staticmethod
    def _hexdump(data: bytes) -> str:
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asciipart = "".join(
                chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08x}  {hexpart:<47}  {asciipart}")
        return "\n".join(lines)

    def _clear_editor(self) -> None:
        self._set_editor_enabled(True)
        self.text.delete("1.0", tk.END)
        self._set_editor_enabled(False)
        self.entry_label.config(text="No file selected")
        self._editable = False

    # ------------------------------------------------------------- editing
    def on_text_modified(self, _event=None) -> None:
        if self.text.edit_modified():
            if getattr(self, "_editable", False):
                self.editor_dirty = True
                self.save_edit_btn.config(state=tk.NORMAL)
                self.revert_btn.config(state=tk.NORMAL)
            self.text.edit_modified(False)

    def commit_text_edit(self) -> None:
        if not (self.current_entry and getattr(self, "_editable", False)):
            return
        text = self.text.get("1.0", "end-1c")
        # idTech text files use latin1 / ascii.  Preserve bytes faithfully.
        data = text.encode("latin1", "replace")
        self.archive.replace_data(self.current_entry.name, data)
        self.editor_dirty = False
        self.dirty = True
        self.save_edit_btn.config(state=tk.DISABLED)
        self.revert_btn.config(state=tk.DISABLED)
        # Update the size column.
        self.tree.set(self.current_entry.name, "size",
                      fmt_size(self.current_entry.length))
        self._update_title()
        self.set_status(f"Edit applied to {self.current_entry.name} "
                        "(remember to Save the archive).")

    def revert_text_edit(self) -> None:
        if self.current_entry:
            self._show_entry(self.current_entry)

    # ------------------------------------------------------------- extract
    def extract_selected(self) -> None:
        if not self.current_entry:
            messagebox.showinfo("Extract", "Select a file first.")
            return
        base = os.path.basename(self.current_entry.name)
        path = filedialog.asksaveasfilename(
            title="Extract file", initialfile=base)
        if not path:
            return
        with open(path, "wb") as fh:
            fh.write(self.current_entry.data or b"")
        self.set_status(f"Extracted {self.current_entry.name} → {path}")

    def extract_all(self) -> None:
        if not self.archive:
            return
        folder = filedialog.askdirectory(title="Extract all into folder")
        if not folder:
            return
        count = 0
        for e in self.archive.entries:
            dest = os.path.join(folder, *e.name.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(e.data or b"")
            count += 1
        self.set_status(f"Extracted {count} files to {folder}")
        messagebox.showinfo("Extract All", f"Extracted {count} files.")

    # ------------------------------------------------------------- mutate
    def replace_from_file(self) -> None:
        if not self.current_entry:
            messagebox.showinfo("Replace", "Select a file to replace first.")
            return
        path = filedialog.askopenfilename(title="Choose replacement bytes")
        if not path:
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self.archive.replace_data(self.current_entry.name, data)
        self.dirty = True
        self.tree.set(self.current_entry.name, "size",
                      fmt_size(self.current_entry.length))
        self._show_entry(self.current_entry)
        self._update_title()
        self.set_status(f"Replaced {self.current_entry.name} "
                        f"({fmt_size(len(data))}).")

    @staticmethod
    def _folder_files(folder: str) -> list[tuple[str, str]]:
        """Return ``(archive_name, disk_path)`` pairs below *folder*.

        Selecting either the extraction root (which contains ``generated``)
        or the ``generated`` directory itself produces archive paths beginning
        with ``generated/``.
        """
        folder = os.path.abspath(folder)
        prefix = ""
        scan_root = folder
        if os.path.basename(folder).lower() == "generated":
            prefix = "generated"
        else:
            # An extraction destination can also contain the source archive or
            # notes.  When it has a generated child, import only that tree.
            generated = next(
                (name for name in os.listdir(folder)
                 if name.lower() == "generated" and
                 os.path.isdir(os.path.join(folder, name))),
                None)
            if generated:
                scan_root = os.path.join(folder, generated)
                prefix = "generated"
        files = []

        def abort_on_walk_error(error: OSError) -> None:
            """Do not silently turn a failed directory scan into an import."""
            raise error

        for directory, dirnames, filenames in os.walk(
                scan_root, onerror=abort_on_walk_error):
            dirnames.sort(key=str.lower)
            filenames.sort(key=str.lower)
            for filename in filenames:
                disk_path = os.path.join(directory, filename)
                relative = os.path.relpath(disk_path, scan_root)
                archive_name = os.path.join(prefix, relative) if prefix \
                    else relative
                files.append((archive_name.replace(os.sep, "/"), disk_path))
        return files

    def replace_add_from_folder(self) -> None:
        if not self.archive:
            messagebox.showinfo("Bulk Replace/Add",
                                "Open an archive first.")
            return
        if self.editor_dirty and not self._confirm_discard_edit():
            return
        folder = filedialog.askdirectory(
            title="Choose folder containing generated (or generated itself)")
        if not folder:
            return

        try:
            files = self._folder_files(folder)
        except OSError as exc:
            messagebox.showerror("Bulk Replace/Add",
                                 f"Could not scan the folder:\n{exc}")
            return
        if not files:
            messagebox.showinfo("Bulk Replace/Add",
                                "The selected folder contains no files.")
            return
        replacements = sum(
            self.archive.find(name) is not None for name, _ in files)
        additions = len(files) - replacements
        if not messagebox.askyesno(
                "Bulk Replace/Add",
                f"Import {len(files)} files from:\n{folder}\n\n"
                f"Replace existing: {replacements}\n"
                f"Add new: {additions}\n\n"
                "Continue?"):
            return

        try:
            loaded = []
            for name, path in files:
                with open(path, "rb") as fh:
                    loaded.append((name, fh.read()))
        except OSError as exc:
            messagebox.showerror("Bulk Replace/Add",
                                 f"Could not read the folder:\n{exc}")
            return

        for name, data in loaded:
            self.archive.upsert_entry(name, data)
        self.dirty = True
        self.editor_dirty = False
        self.current_entry = None
        self.refresh_list()
        self._clear_editor()
        self._update_title()
        self.set_status(
            f"Folder imported: replaced {replacements}, added {additions} "
            "(remember to Save the archive).")
        messagebox.showinfo(
            "Bulk Replace/Add complete",
            f"Replaced {replacements} files and added {additions} files.\n\n"
            "Use File > Save to write the changes to the archive.")

    def add_file(self) -> None:
        if not self.archive:
            messagebox.showinfo("Add", "Open an archive first.")
            return
        path = filedialog.askopenfilename(title="Choose file to add")
        if not path:
            return
        default_name = os.path.basename(path)
        name = self._ask_string(
            "Add file",
            "Internal archive path (use forward slashes):",
            default_name)
        if not name:
            return
        if self.archive.find(name):
            messagebox.showerror("Add", f"'{name}' already exists.")
            return
        with open(path, "rb") as fh:
            self.archive.add_entry(name, fh.read())
        self.dirty = True
        self.refresh_list()
        self._update_title()
        self.set_status(f"Added {name}")

    def delete_selected(self) -> None:
        if not self.current_entry:
            return
        if not messagebox.askyesno(
                "Delete", f"Remove '{self.current_entry.name}' "
                          "from the archive?"):
            return
        name = self.current_entry.name
        self.archive.remove_entry(name)
        self.current_entry = None
        self.dirty = True
        self.refresh_list()
        self._clear_editor()
        self._update_title()
        self.set_status(f"Deleted {name}")

    # ------------------------------------------------------------- dialogs
    def _ask_string(self, title: str, prompt: str, initial: str) -> str | None:
        from tkinter import simpledialog
        return simpledialog.askstring(title, prompt, initialvalue=initial,
                                      parent=self.root)

    def show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"{APP_TITLE}\n\n"
            "Reads and writes DOOM 3 BFG Edition .resources container files\n"
            "(PC and Xbox 360 builds), based on id Software's open-source\n"
            "File_Resource.cpp format.\n\n"
            "Edit text decls (.script/.def/.gui/.mtr/…) directly, or\n"
            "extract / replace / add / delete any entry, then Save to\n"
            "rebuild a valid archive.")

    # ------------------------------------------------------------- close
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(
            "Unsaved changes",
            "The archive has unsaved changes. Save before continuing?")
        if ans is None:
            return False
        if ans:
            self.save_archive()
            return not self.dirty
        return True

    def _confirm_discard_edit(self) -> bool:
        ans = messagebox.askyesno(
            "Unsaved edit",
            "You have an uncommitted text edit. Discard it?")
        return ans

    def on_close(self) -> None:
        if self._confirm_discard():
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    app = ResourceEditorApp(root)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app._load_path(sys.argv[1])
    root.mainloop()


if __name__ == "__main__":
    main()
