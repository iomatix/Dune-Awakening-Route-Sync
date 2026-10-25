import asyncio
from pathlib import Path
from typing import Dict, List, Optional
from nicegui import app, ui

from dune_ops.catalogs import CatalogRegistry
from dune_ops.cluster_discovery import ClusterDiscoveryEngine
from dune_ops.i18n import I18nEngine
from dune_ops.map_client import MapServerClient
from dune_ops.models import MapInstanceDTO, PlayerLiveStateDTO, ServerInstanceStatus


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
            self.command_output_log.append(f"[ERROR] Map discovery failed: {e}")

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
            return
        if not self.active_map or not self.active_map.pod_name:
            self.command_output_log.append("[FAIL] No active map selected.")
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
        ui.colors(primary="#d97706", secondary="#78350f", accent="#f59e0b", dark="#111827")

        # Dynamic reactive labels holder
        labels: Dict[str, ui.label] = {}

        # ----------------- HEADER -----------------
        with ui.header().classes("items-center justify-between bg-stone-900 border-b border-stone-800 px-6 py-3"):
            with ui.row().classes("items-center gap-4"):
                ui.icon("terminal", size="md").classes("text-amber-500")
                with ui.column().classes("gap-0"):
                    labels["app_title"] = ui.label(app_state.i18n.t("app.title")).classes("text-lg font-bold text-amber-100")
                    labels["app_subtitle"] = ui.label(app_state.i18n.t("app.subtitle")).classes("text-xs text-stone-400")

            with ui.row().classes("items-center gap-4"):
                # Active Map Selection
                labels["nav_map"] = ui.label(app_state.i18n.t("nav.active_map")).classes("text-sm text-stone-300")
                map_options = {m.map_id: f"{m.map_id} ({m.status.value})" for m in app_state.available_maps}
                current_map_id = app_state.active_map.map_id if app_state.active_map else None

                map_select = ui.select(
                    options=map_options,
                    value=current_map_id,
                    on_change=lambda e: on_map_selected(e.value),
                ).classes("w-48 bg-stone-800 text-stone-100 border border-stone-700 rounded")

                # Language switch
                labels["nav_lang"] = ui.label(app_state.i18n.t("nav.language")).classes("text-sm text-stone-300")
                ui.select(
                    options={"pl": "Polski", "en": "English"},
                    value=app_state.i18n.current_lang,
                    on_change=lambda e: on_lang_changed(e.value),
                ).classes("w-28 bg-stone-800 text-stone-100 border border-stone-700 rounded")

                # Database Catalog Sync Button
                ui.button(
                    icon="sync",
                    on_click=lambda: on_sync_db_catalog(),
                ).props("flat dense color=amber").tooltip("Sync items from PostgreSQL")

        # ----------------- TABS NAVIGATION (Zero reload) -----------------
        with ui.tabs().classes("w-full bg-stone-950 text-stone-400 border-b border-stone-800") as tabs:
            tab_players = ui.tab("tab_players", label=app_state.i18n.t("nav.tab_players"), icon="group")
            tab_console = ui.tab("tab_console", label=app_state.i18n.t("nav.tab_console"), icon="code")

        with ui.tab_panels(tabs, value=tab_players).classes("w-full bg-stone-900 p-6 flex-grow"):
            # ----------------- TAB 1: PLAYERS & INVENTORY -----------------
            with ui.tab_panel(tab_players):
                with ui.row().classes("w-full justify-between items-center mb-4"):
                    labels["players_status"] = ui.label(
                        f"{app_state.i18n.t('players.no_players_online') if not app_state.players else f'Online: {len(app_state.players)}'}"
                    ).classes("text-stone-300 text-sm")
                    ui.button(
                        label=app_state.i18n.t("nav.refresh"),
                        icon="refresh",
                        on_click=lambda: on_refresh_player_view(),
                    ).props("outline color=amber")

                # Players Table
                columns = [
                    {"name": "character_name", "label": app_state.i18n.t("players.col_name"), "field": "character_name", "align": "left"},
                    {"name": "steam_id", "label": app_state.i18n.t("players.col_steam_id"), "field": "steam_id", "align": "left"},
                    {"name": "ping_ms", "label": app_state.i18n.t("players.col_ping"), "field": "ping_ms", "align": "center"},
                    {"name": "coordinates", "label": app_state.i18n.t("players.col_coords"), "field": lambda p: f"X:{p['coordinates']['x']:.1f} Y:{p['coordinates']['y']:.1f} Z:{p['coordinates']['z']:.1f}", "align": "left"},
                ]
                player_rows = [p.model_dump() for p in app_state.players]
                table = ui.table(columns=columns, rows=player_rows, row_key="steam_id").classes("w-full border border-stone-800 bg-stone-950")

                # Detailed Inventory & Coordinates Inspection Container
                inventory_container = ui.column().classes("w-full mt-6 gap-4")

                def render_inventory_view():
                    inventory_container.clear()
                    with inventory_container:
                        if not app_state.selected_player:
                            return
                        p = app_state.selected_player
                        ui.label(f"{p.character_name} ({p.steam_id})").classes("text-lg font-bold text-amber-400")
                        with ui.row().classes("w-full gap-6"):
                            # Equipped
                            with ui.card().classes("flex-1 bg-stone-950 border border-stone-800"):
                                ui.label(app_state.i18n.t("players.equipped_gear")).classes("font-semibold text-stone-200")
                                if not p.equipped_items:
                                    ui.label(app_state.i18n.t("players.empty_inventory")).classes("text-xs text-stone-500 italic")
                                for itm in p.equipped_items:
                                    ui.label(f"[{itm.slot_type}] {itm.display_name} (x{itm.quantity})").classes("text-sm text-stone-300")
                            # Backpack
                            with ui.card().classes("flex-1 bg-stone-950 border border-stone-800"):
                                ui.label(app_state.i18n.t("players.inventory")).classes("font-semibold text-stone-200")
                                if not p.inventory_items:
                                    ui.label(app_state.i18n.t("players.empty_inventory")).classes("text-xs text-stone-500 italic")
                                for itm in p.inventory_items:
                                    ui.label(f"[{itm.slot_type}] {itm.display_name} (x{itm.quantity})").classes("text-sm text-stone-300")

                table.on("rowClick", lambda e: on_select_player_row(e.args[1]))

            # ----------------- TAB 2: DEV CONSOLE & SPAWNER -----------------
            with ui.tab_panel(tab_console):
                with ui.row().classes("w-full gap-6"):
                    # Left: Command Input & Quick Spawner
                    with ui.column().classes("flex-1 gap-4"):
                        with ui.card().classes("w-full bg-stone-950 border border-stone-800 p-4"):
                            labels["quick_spawn_title"] = ui.label(app_state.i18n.t("console.quick_spawn")).classes("text-base font-semibold text-amber-400 mb-2")

                            # Item Auto-completion selection
                            item_names = [itm.item_id for itm in app_state.catalogs.items.values()]
                            item_input = ui.select(
                                options=item_names,
                                with_input=True,
                                label=app_state.i18n.t("console.item_select"),
                            ).classes("w-full bg-stone-900 border border-stone-700 rounded")

                            with ui.row().classes("w-full items-center gap-4 mt-2"):
                                qty_input = ui.number(label=app_state.i18n.t("console.quantity"), value=1, min=1, max=1000000).classes("w-32 bg-stone-900")
                                ui.button(
                                    label=app_state.i18n.t("console.spawn_button"),
                                    icon="inventory_2",
                                    on_click=lambda: on_quick_spawn(item_input.value, int(qty_input.value or 1)),
                                ).props("color=amber")

                        with ui.card().classes("w-full bg-stone-950 border border-stone-800 p-4"):
                            cmd_input = ui.input(
                                placeholder=app_state.i18n.t("console.command_placeholder"),
                            ).classes("w-full bg-stone-900 border border-stone-700 text-stone-100 rounded px-3")

                            with ui.row().classes("w-full justify-between mt-2"):
                                ui.button(
                                    label=app_state.i18n.t("console.execute"),
                                    icon="play_arrow",
                                    on_click=lambda: on_execute_command(cmd_input.value),
                                ).props("color=amber")
                                ui.button(
                                    label=app_state.i18n.t("console.clear"),
                                    icon="delete_sweep",
                                    on_click=lambda: on_clear_console(),
                                ).props("flat color=stone")

                    # Right: Output Logs
                    with ui.column().classes("flex-1"):
                        with ui.card().classes("w-full bg-stone-950 border border-stone-800 p-4 min-h-[400px]"):
                            labels["log_title"] = ui.label(app_state.i18n.t("console.output_title")).classes("text-sm font-semibold text-stone-400 mb-2")
                            log_container = ui.log().classes("w-full h-80 font-mono text-xs text-amber-200/90")
                            for line in app_state.command_output_log:
                                log_container.push(line)

        # ----------------- EVENT HANDLERS -----------------
        def on_map_selected(map_id: str):
            selected = next((m for m in app_state.available_maps if m.map_id == map_id), None)
            if selected:
                app_state.active_map = selected
                on_refresh_player_view()

        def on_refresh_player_view():
            app_state.refresh_players(force=True)
            table.rows = [p.model_dump() for p in app_state.players]
            labels["players_status"].set_text(
                app_state.i18n.t("players.no_players_online") if not app_state.players else f"Online: {len(app_state.players)}"
            )
            table.update()

        def on_select_player_row(row_data: dict):
            steam_id = row_data.get("steam_id")
            app_state.selected_player = next((p for p in app_state.players if p.steam_id == steam_id), None)
            render_inventory_view()

        def on_sync_db_catalog():
            ui.notify("Synchronizing database catalog...", color="warning")
            success, count, msg = app_state.catalogs.sync_from_database()
            if success:
                ui.notify(f"Synced {count} items from database!", color="positive")
                app_state.command_output_log.append(f"[CATALOG SYNC] {msg}")
            else:
                ui.notify(f"Sync failed: {msg}", color="negative")
                app_state.command_output_log.append(f"[CATALOG SYNC ERROR] {msg}")
            log_container.push(app_state.command_output_log[-1])

        def on_execute_command(raw_cmd: str):
            app_state.execute_raw_command(raw_cmd)
            if app_state.command_output_log:
                log_container.push(app_state.command_output_log[-1])

        def on_quick_spawn(item_id: Optional[str], qty: int):
            if not item_id:
                ui.notify("Select an item first", color="negative")
                return
            target = app_state.selected_player.character_name if app_state.selected_player else "Player"
            cmd = f"GiveItem {target} {item_id} {qty}"
            on_execute_command(cmd)

        def on_clear_console():
            app_state.command_output_log.clear()
            log_container.clear()

        def on_lang_changed(lang_code: str):
            app_state.i18n.set_language(lang_code)
            labels["app_title"].set_text(app_state.i18n.t("app.title"))
            labels["app_subtitle"].set_text(app_state.i18n.t("app.subtitle"))
            labels["nav_map"].set_text(app_state.i18n.t("nav.active_map"))
            labels["nav_lang"].set_text(app_state.i18n.t("nav.language"))
            tab_players.set_text(app_state.i18n.t("nav.tab_players"))
            tab_console.set_text(app_state.i18n.t("nav.tab_console"))
            on_refresh_player_view()


def run_app():
    state = DuneOpsApp()
    init_ui(state)
    ui.run(title="Dune: Awakening - Operator Console", port=8090, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    run_app()