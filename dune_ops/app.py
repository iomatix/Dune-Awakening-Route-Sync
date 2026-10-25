from pathlib import Path
from typing import Dict, List, Optional
from nicegui import ui

from dune_ops.catalogs import CatalogRegistry
from dune_ops.cluster_discovery import ClusterDiscoveryEngine
from dune_ops.i18n import I18nEngine
from dune_ops.map_client import MapServerClient
from dune_ops.models import MapInstanceDTO, PlayerLiveStateDTO


class DuneOpsApp:
    def __init__(self):
        root_dir = Path(__file__).resolve().parent
        self.i18n = I18nEngine(locales_dir=root_dir / "locales", default_lang="pl")
        self.catalogs = CatalogRegistry(data_dir=root_dir / "data")
        self.discovery = ClusterDiscoveryEngine(vm_user="dune", vm_ip="192.168.1.57")
        self.client = MapServerClient(vm_user="dune", vm_ip="192.168.1.57")

        self.active_map: Optional[MapInstanceDTO] = None
        self.available_maps: List[MapInstanceDTO] = []
        self.players: List[PlayerLiveStateDTO] = []
        self.selected_player: Optional[PlayerLiveStateDTO] = None
        self.command_output_log: List[str] = []

    def refresh_maps(self) -> None:
        try:
            self.available_maps = self.discovery.get_map_instances()
            ready_maps = [m for m in self.available_maps if m.is_ready]
            if ready_maps and (not self.active_map or self.active_map.map_id not in [m.map_id for m in ready_maps]):
                self.active_map = ready_maps[0]
        except Exception as e:
            self.command_output_log.append(self.i18n.t("logs.map_discovery_error", error=str(e)))

    def refresh_players(self, force: bool = False) -> None:
        if not self.active_map or not self.active_map.pod_name:
            self.players = []
            return
        ns = self.discovery.resolve_namespace() or ""
        self.players = self.client.get_live_players(
            namespace=ns,
            pod_name=self.active_map.pod_name,
            map_id=self.active_map.map_id,
            force_refresh=force,
        )

    def execute_raw_command(self, cmd_str: str) -> None:
        cmd = cmd_str.strip()
        if not cmd:
            self.command_output_log.append(f"[WARN] {self.i18n.t('notify.empty_command')}")
            return
        if not self.active_map or not self.active_map.pod_name:
            self.command_output_log.append(f"[FAIL] {self.i18n.t('notify.no_active_map')}")
            return

        ns = self.discovery.resolve_namespace() or ""
        success, msg = self.client.execute_command(
            namespace=ns,
            pod_name=self.active_map.pod_name,
            command_string=cmd,
        )
        tag = "[SUCCESS]" if success else "[FAIL]"
        self.command_output_log.append(f"{tag} ({self.active_map.map_id}) > {cmd} | {msg}")


def init_ui(app_state: DuneOpsApp):
    app_state.refresh_maps()
    app_state.refresh_players()

    @ui.page("/")
    def main_page():
        dark = ui.dark_mode()
        dark.enable()

        ui.colors(
            primary="#f59e0b",
            secondary="#b45309",
            accent="#d97706",
            dark="#0c0d0e",
            positive="#10b981",
            negative="#ef4444",
            warning="#f59e0b",
            info="#0ea5e9",
        )

        ui.query("body").classes("bg-[#0c0d0e] text-stone-200 m-0 p-0 antialiased overflow-x-hidden")
        ui.query(".nicegui-content").classes("p-0 m-0 w-full min-h-screen bg-[#0c0d0e] flex flex-col")

        labels: Dict[str, ui.label] = {}
        tooltips: Dict[str, ui.tooltip] = {}

        def get_map_status_text(status_val: str) -> str:
            key = f"maps.status_{status_val.lower()}"
            return app_state.i18n.t(key)

        def build_map_options() -> Dict[str, str]:
            return {
                m.map_id: f"{m.map_id} ({get_map_status_text(m.status.value)})"
                for m in app_state.available_maps
            }

        # ----------------- KONTENER STRONY -----------------
        with ui.column().classes("w-full min-h-screen bg-[#0c0d0e] p-0 gap-0 flex-grow"):

            # ----------------- NAGŁÓWEK -----------------
            with ui.row().classes(
                "w-full items-center justify-between bg-[#141518] border-b border-stone-800/80 px-6 py-3 shadow-md"
            ):
                with ui.row().classes("items-center gap-3"):
                    with ui.avatar(icon="terminal", color="amber-9", text_color="amber-1").classes("shadow"):
                        pass
                    with ui.column().classes("gap-0"):
                        labels["app_title"] = ui.label(app_state.i18n.t("app.title")).classes(
                            "text-base font-bold tracking-wide text-amber-400"
                        )
                        labels["app_subtitle"] = ui.label(app_state.i18n.t("app.subtitle")).classes(
                            "text-xs text-stone-400"
                        )

                with ui.row().classes("items-center gap-4"):
                    labels["nav_map"] = ui.label(app_state.i18n.t("nav.active_map")).classes(
                        "text-xs font-semibold uppercase tracking-wider text-stone-400"
                    )
                    current_map_id = app_state.active_map.map_id if app_state.active_map else None

                    map_select = ui.select(
                        options=build_map_options(),
                        value=current_map_id,
                        on_change=lambda e: on_map_selected(e.value),
                    ).props("dark outlined dense options-dense").classes("w-56 bg-[#1b1c20] text-sm rounded")

                    labels["nav_lang"] = ui.label(app_state.i18n.t("nav.language")).classes(
                        "text-xs font-semibold uppercase tracking-wider text-stone-400"
                    )
                    ui.select(
                        options={"pl": "Polski", "en": "English"},
                        value=app_state.i18n.current_lang,
                        on_change=lambda e: on_lang_changed(e.value),
                    ).props("dark outlined dense options-dense").classes("w-28 bg-[#1b1c20] text-sm rounded")

                    with ui.button(icon="sync", on_click=lambda: on_sync_db_catalog()).props(
                        "flat round dense color=amber"
                    ):
                        tooltips["sync_db"] = ui.tooltip(app_state.i18n.t("nav.sync_tooltip"))

            # ----------------- ZAKŁADKI -----------------
            with ui.tabs().classes(
                "w-full bg-[#101114] text-stone-400 border-b border-stone-800/80 px-4"
            ).props("indicator-color=amber active-color=amber inline-label dense") as tabs:
                tab_players = ui.tab("tab_players", label=app_state.i18n.t("nav.tab_players"), icon="group")
                tab_console = ui.tab("tab_console", label=app_state.i18n.t("nav.tab_console"), icon="terminal")

            # ----------------- WIDOKI ZAKŁADEK -----------------
            with ui.tab_panels(tabs, value=tab_players).classes("w-full bg-[#0c0d0e] p-6 flex-grow"):

                # ================= TAB 1: GRACZE I EKWIPUNEK =================
                with ui.tab_panel(tab_players).classes("p-0 gap-6 flex flex-col"):
                    with ui.row().classes("w-full justify-between items-center bg-[#141518] p-4 rounded-lg border border-stone-800"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("radio_button_checked", size="xs").classes("text-emerald-400")
                            labels["players_status"] = ui.label(
                                app_state.i18n.t("players.no_players_online")
                                if not app_state.players
                                else app_state.i18n.t("players.active_players_count", count=len(app_state.players))
                            ).classes("text-stone-200 text-sm font-medium")

                        btn_refresh_players = ui.button(
                            app_state.i18n.t("nav.refresh"),
                            icon="refresh",
                            on_click=lambda: on_refresh_player_view(),
                        ).props("dark outline dense color=amber").classes("px-3")

                    def build_columns() -> List[dict]:
                        return [
                            {"name": "character_name", "label": app_state.i18n.t("players.col_name"), "field": "character_name", "align": "left", "classes": "font-bold text-amber-300"},
                            {"name": "steam_id", "label": app_state.i18n.t("players.col_steam_id"), "field": "steam_id", "align": "left", "classes": "font-mono text-stone-400 text-xs"},
                            {"name": "ping_display", "label": app_state.i18n.t("players.col_ping"), "field": "ping_display", "align": "center", "classes": "font-mono text-emerald-400"},
                            {"name": "coords_display", "label": app_state.i18n.t("players.col_coords"), "field": "coords_display", "align": "left", "classes": "font-mono text-xs text-stone-300"},
                        ]

                    def build_player_rows() -> List[dict]:
                        rows = []
                        for p in app_state.players:
                            row = p.model_dump()
                            row["ping_display"] = f"{p.ping_ms} ms"
                            row["coords_display"] = f"X: {p.coordinates.x:.1f}  Y: {p.coordinates.y:.1f}  Z: {p.coordinates.z:.1f}"
                            rows.append(row)
                        return rows

                    table = (
                        ui.table(columns=build_columns(), rows=build_player_rows(), row_key="steam_id")
                        .classes("w-full bg-[#141518] text-stone-200 rounded-lg border border-stone-800")
                        .props("dark flat bordered separator=horizontal")
                    )

                    inventory_container = ui.column().classes("w-full mt-2 gap-4")

                    def render_inventory_view():
                        inventory_container.clear()
                        with inventory_container:
                            if not app_state.selected_player:
                                return
                            p = app_state.selected_player
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("person_search", color="amber").classes("text-xl")
                                ui.label(f"{p.character_name} ({app_state.i18n.t('players.steam_label', steam_id=p.steam_id)})").classes(
                                    "text-lg font-bold text-amber-400 tracking-wide"
                                )

                            with ui.row().classes("w-full gap-6"):
                                with ui.card().classes("flex-1 bg-[#141518] border border-stone-800 p-4 rounded-lg shadow"):
                                    with ui.row().classes("items-center gap-2 mb-2"):
                                        ui.icon("shield", size="xs").classes("text-amber-500")
                                        ui.label(app_state.i18n.t("players.equipped_gear")).classes("font-semibold text-stone-200 text-sm")
                                    if not p.equipped_items:
                                        ui.label(app_state.i18n.t("players.empty_inventory")).classes("text-xs text-stone-500 italic")
                                    for itm in p.equipped_items:
                                        with ui.row().classes("w-full justify-between border-b border-stone-800/50 py-1 text-xs"):
                                            ui.label(f"[{itm.slot_type}] {itm.display_name}").classes("text-stone-300 font-medium")
                                            ui.label(f"x{itm.quantity}").classes("text-amber-400 font-mono")

                                with ui.card().classes("flex-1 bg-[#141518] border border-stone-800 p-4 rounded-lg shadow"):
                                    with ui.row().classes("items-center gap-2 mb-2"):
                                        ui.icon("backpack", size="xs").classes("text-amber-500")
                                        ui.label(app_state.i18n.t("players.inventory")).classes("font-semibold text-stone-200 text-sm")
                                    if not p.inventory_items:
                                        ui.label(app_state.i18n.t("players.empty_inventory")).classes("text-xs text-stone-500 italic")
                                    for itm in p.inventory_items:
                                        with ui.row().classes("w-full justify-between border-b border-stone-800/50 py-1 text-xs"):
                                            ui.label(f"[{itm.slot_type}] {itm.display_name}").classes("text-stone-300 font-medium")
                                            ui.label(f"x{itm.quantity}").classes("text-amber-400 font-mono")

                    table.on("rowClick", lambda e: on_select_player_row(e.args[1] if len(e.args) > 1 and isinstance(e.args[1], dict) else {}))

                # ================= TAB 2: KONSOLA DEV & GENERATOR =================
                with ui.tab_panel(tab_console).classes("p-0 gap-6 flex flex-col"):
                    with ui.row().classes("w-full gap-6 items-start"):
                        with ui.column().classes("flex-1 gap-6"):
                            with ui.card().classes("w-full bg-[#141518] border border-stone-800 p-5 rounded-lg shadow"):
                                with ui.row().classes("items-center gap-2 mb-3"):
                                    ui.icon("auto_fix_high", size="xs").classes("text-amber-400")
                                    labels["quick_spawn_title"] = ui.label(app_state.i18n.t("console.quick_spawn")).classes(
                                        "text-sm font-semibold uppercase tracking-wider text-amber-400"
                                    )

                                item_names = [itm.item_id for itm in app_state.catalogs.items.values()]
                                item_input = ui.select(
                                    options=item_names,
                                    with_input=True,
                                    label=app_state.i18n.t("console.item_select"),
                                ).props("dark outlined dense options-dense").classes("w-full bg-[#1b1c20] rounded")

                                with ui.row().classes("w-full items-center gap-4 mt-3"):
                                    qty_input = ui.number(
                                        label=app_state.i18n.t("console.quantity"),
                                        value=1,
                                        min=1,
                                        max=1000000,
                                    ).props("dark outlined dense").classes("w-36 bg-[#1b1c20] rounded")

                                    btn_spawn = ui.button(
                                        app_state.i18n.t("console.spawn_button"),
                                        icon="inventory_2",
                                        on_click=lambda: on_quick_spawn(item_input.value, int(qty_input.value or 1)),
                                    ).props("dark unelevated color=amber text-color=dark").classes("px-5 font-semibold")

                            with ui.card().classes("w-full bg-[#141518] border border-stone-800 p-5 rounded-lg shadow"):
                                with ui.row().classes("items-center gap-2 mb-3"):
                                    ui.icon("terminal", size="xs").classes("text-amber-400")
                                    labels["raw_cmd_title"] = ui.label(app_state.i18n.t("console.raw_command_title")).classes(
                                        "text-sm font-semibold uppercase tracking-wider text-amber-400"
                                    )

                                cmd_input = ui.input(
                                    placeholder=app_state.i18n.t("console.command_placeholder"),
                                ).props("dark outlined dense").classes("w-full bg-[#1b1c20] font-mono text-sm rounded")

                                with ui.row().classes("w-full justify-between items-center mt-3"):
                                    btn_execute = ui.button(
                                        app_state.i18n.t("console.execute"),
                                        icon="play_arrow",
                                        on_click=lambda: on_execute_command(cmd_input.value),
                                    ).props("dark unelevated color=amber text-color=dark font-semibold").classes("px-4")

                                    btn_clear = ui.button(
                                        app_state.i18n.t("console.clear"),
                                        icon="delete_sweep",
                                        on_click=lambda: on_clear_console(),
                                    ).props("dark flat dense color=stone").classes("text-stone-400 hover:text-stone-100")

                        with ui.column().classes("flex-1 h-full"):
                            with ui.card().classes("w-full bg-[#141518] border border-stone-800 p-5 rounded-lg shadow flex flex-col"):
                                with ui.row().classes("items-center justify-between mb-2"):
                                    with ui.row().classes("items-center gap-2"):
                                        ui.icon("history", size="xs").classes("text-amber-400")
                                        labels["log_title"] = ui.label(app_state.i18n.t("console.output_title")).classes(
                                            "text-sm font-semibold uppercase tracking-wider text-stone-300"
                                        )
                                    with ui.button(icon="cleaning_services", on_click=lambda: on_clear_console()).props(
                                        "flat round dense color=stone"
                                    ):
                                        tooltips["clear_logs"] = ui.tooltip(app_state.i18n.t("console.clear_tooltip"))

                                log_container = ui.log().classes(
                                    "w-full h-96 bg-[#0c0d0e] p-3 rounded border border-stone-800 font-mono text-xs text-amber-200/90 leading-relaxed shadow-inner"
                                )
                                for line in app_state.command_output_log:
                                    log_container.push(line)

        # ----------------- OBSŁUGA ZDARZEŃ -----------------
        def on_map_selected(map_id: str):
            selected = next((m for m in app_state.available_maps if m.map_id == map_id), None)
            if selected:
                app_state.active_map = selected
                on_refresh_player_view()

        def on_refresh_player_view():
            app_state.refresh_players(force=True)
            table.columns = build_columns()
            table.rows = build_player_rows()
            labels["players_status"].set_text(
                app_state.i18n.t("players.no_players_online")
                if not app_state.players
                else app_state.i18n.t("players.active_players_count", count=len(app_state.players))
            )
            table.update()

        def on_select_player_row(row_data: dict):
            steam_id = row_data.get("steam_id")
            if steam_id:
                app_state.selected_player = next((p for p in app_state.players if p.steam_id == steam_id), None)
                render_inventory_view()

        def on_sync_db_catalog():
            ui.notify(app_state.i18n.t("notify.sync_in_progress"), color="warning")
            success, count, msg = app_state.catalogs.sync_from_database()
            if success:
                ui.notify(app_state.i18n.t("notify.sync_success", count=count), color="positive")
                app_state.command_output_log.append(app_state.i18n.t("logs.catalog_synced", msg=msg))
                item_input.options = [itm.item_id for itm in app_state.catalogs.items.values()]
                item_input.update()
            else:
                ui.notify(app_state.i18n.t("notify.sync_failed", error=msg), color="negative")
                app_state.command_output_log.append(app_state.i18n.t("logs.catalog_error", msg=msg))
            log_container.push(app_state.command_output_log[-1])

        def on_execute_command(raw_cmd: str):
            app_state.execute_raw_command(raw_cmd)
            if app_state.command_output_log:
                log_container.push(app_state.command_output_log[-1])

        def on_quick_spawn(item_id: Optional[str], qty: int):
            if not item_id:
                ui.notify(app_state.i18n.t("notify.select_item_first"), color="negative")
                return
            target = app_state.selected_player.character_name if app_state.selected_player else "Player"
            cmd = f"GiveItem {target} {item_id} {qty}"
            on_execute_command(cmd)

        def on_clear_console():
            app_state.command_output_log.clear()
            log_container.clear()

        def on_lang_changed(lang_code: str):
            app_state.i18n.set_language(lang_code)

            # Aktualizacja etykiet tekstowych
            labels["app_title"].set_text(app_state.i18n.t("app.title"))
            labels["app_subtitle"].set_text(app_state.i18n.t("app.subtitle"))
            labels["nav_map"].set_text(app_state.i18n.t("nav.active_map"))
            labels["nav_lang"].set_text(app_state.i18n.t("nav.language"))
            labels["quick_spawn_title"].set_text(app_state.i18n.t("console.quick_spawn"))
            labels["raw_cmd_title"].set_text(app_state.i18n.t("console.raw_command_title"))
            labels["log_title"].set_text(app_state.i18n.t("console.output_title"))

            # Aktualizacja zakładek (poprzez właściwość _props['label'] w Quasar)
            tab_players._props["label"] = app_state.i18n.t("nav.tab_players")
            tab_players.update()
            tab_console._props["label"] = app_state.i18n.t("nav.tab_console")
            tab_console.update()

            # Aktualizacja przycisków
            btn_refresh_players.set_text(app_state.i18n.t("nav.refresh"))
            btn_spawn.set_text(app_state.i18n.t("console.spawn_button"))
            btn_execute.set_text(app_state.i18n.t("console.execute"))
            btn_clear.set_text(app_state.i18n.t("console.clear"))

            # Aktualizacja formularzy i tooltipów
            item_input._props["label"] = app_state.i18n.t("console.item_select")
            item_input.update()
            qty_input._props["label"] = app_state.i18n.t("console.quantity")
            qty_input.update()
            cmd_input._props["placeholder"] = app_state.i18n.t("console.command_placeholder")
            cmd_input.update()

            tooltips["sync_db"].set_text(app_state.i18n.t("nav.sync_tooltip"))
            tooltips["clear_logs"].set_text(app_state.i18n.t("console.clear_tooltip"))

            map_select.options = build_map_options()
            map_select.update()

            on_refresh_player_view()
            if app_state.selected_player:
                render_inventory_view()


def run_app():
    state = DuneOpsApp()
    init_ui(state)
    ui.run(title="Dune: Awakening - Operator Console", port=8090, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    run_app()