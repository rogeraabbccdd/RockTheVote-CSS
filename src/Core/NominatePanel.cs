using CounterStrikeSharp.API;
using CounterStrikeSharp.API.Core;
using CounterStrikeSharp.API.Modules.Extensions;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace cs2_rockthevote.Core
{
    /// Paged map picker with thumbnails for !nominate (Nominate.MenuType = "panorama").
    /// One custom_hud_layout entity is shared by every player who has it open; each player's
    /// page is written with the *ForPlayer calls, so players browse independently.
    public class NominatePanel : IPluginDependency<Plugin, Config>
    {
        public const int PageSize = 12;

        private const string VoteDialogPanel = "rtv_vote_dialog";
        private const string DialogPanel = "rtv_nom_dialog";
        private const string HeaderPanel = "rtv_nom_header";
        private const string PagePanel = "rtv_nom_page";
        private const string PrevPanel = "rtv_nom_prev";
        private const string NextPanel = "rtv_nom_next";
        private const string ClosePanel = "rtv_nom_close";
        private const string CardPrefix = "rtv_nom_card_";
        private const string ImagePrefix = "rtv_nom_img_";
        private const string NamePrefix = "rtv_nom_name_";

        private const string OpenClass = "rtv-nom-open";
        private const string DisabledClass = "rtv-nom-disabled";
        private const string MineClass = "rtv-nom-mine";
        private const string ButtonOffClass = "rtv-nom-btn-off";
        private const string EmptyClass = "rtv-nom-empty";
        private const string VoteDialogHiddenClass = "rtv-dialog-hidden";
        private const string ImageOnClass = "rtv-img-on";

        private sealed class View(List<Map> maps, bool isSearch)
        {
            public List<Map> Maps { get; } = maps;
            public bool IsSearch { get; } = isSearch;
            public int Page { get; set; }
            public int PageCount => Math.Max(1, (Maps.Count + PageSize - 1) / PageSize);
        }

        private readonly ILogger<NominatePanel> _logger;
        private readonly NominationCommand _nominations;
        private readonly StringLocalizer _localizer;
        private readonly MapCooldown _mapCooldown;
        private readonly PluginState _pluginState;
        private ILogger _debugLogger = NullLogger.Instance;
        private PanoramaMenuConfig _panoramaConfig = new();
        private NominateConfig _nomConfig = new();
        private Plugin? _plugin;
        private CCSCustomHudLayout? _hud;

        private readonly Dictionary<int, View> _views = new();

        // Picture class currently set on each card, per slot. Classes accumulate on the entity,
        // so the previous one has to be removed before the next is added. Kept until the entity
        // is destroyed, because the entity still holds them after the player closes the panel.
        private readonly Dictionary<int, string?[]> _appliedImages = new();

        public NominatePanel(NominationCommand nominations, StringLocalizer localizer, MapCooldown mapCooldown, PluginState pluginState, ILogger<NominatePanel> logger)
        {
            _nominations = nominations;
            _localizer = localizer;
            _mapCooldown = mapCooldown;
            _pluginState = pluginState;
            _logger = logger;
        }

        public void OnLoad(Plugin plugin)
        {
            _plugin = plugin;
            _nominations.AttachPanel(this);
            plugin.RegisterListener<Listeners.OnCustomHudClicked>(OnHudClicked);
            plugin.RegisterEventHandler<EventPlayerDisconnect>(OnPlayerDisconnect, HookMode.Pre);
        }

        public void OnConfigParsed(Config config)
        {
            _panoramaConfig = config.PanoramaMenu;
            _nomConfig = config.Nominate;
            _debugLogger = DebugLog.For(_logger, config);
        }

        public void OnMapStart(string mapName)
        {
            Destroy();
        }

        public void Unload(Plugin plugin)
        {
            plugin.RemoveListener<Listeners.OnCustomHudClicked>(OnHudClicked);
            Destroy();
        }

        // Opens (or replaces) this player's panel on page 1. False when the panel cannot be
        // shown, so the caller can fall back to the regular menu.
        public bool Open(CCSPlayerController player, IReadOnlyList<Map> maps, bool isSearch)
        {
            if (!player.IsValid || maps.Count == 0)
                return false;

            try
            {
                if (!EnsureHud(out var hud))
                    return false;

                var view = new View([.. maps], isSearch);
                _views[player.Slot] = view;

                Render(hud, player, view);
                hud.SetHasClassForPlayer(player, DialogPanel, OpenClass, true);
                hud.SetInputCaptureEnabled(player, true);

                _debugLogger.LogInformation("[RTV.NominatePanel] Opened for slot {Slot}: {Count} maps, search={IsSearch}.",
                    player.Slot, maps.Count, isSearch);
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[RTV.NominatePanel] Failed to open the nominate panel for slot {Slot}.", player.Slot);
                _views.Remove(player.Slot);
                if (_views.Count == 0)
                    Destroy();
                return false;
            }
        }

        public void Close(CCSPlayerController player)
        {
            if (!_views.Remove(player.Slot))
                return;

            if (_hud is { IsValid: true } hud && player.IsValid)
            {
                try
                {
                    hud.SetHasClassForPlayer(player, DialogPanel, OpenClass, false);
                    hud.SetInputCaptureEnabled(player, false);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "[RTV.NominatePanel] Failed to close the nominate panel for slot {Slot}.", player.Slot);
                }
            }

            if (_views.Count == 0)
                Destroy();
        }

        // Closes every open panel (e.g. when the end of map vote starts and needs the cursor)
        public void CloseAll()
        {
            if (_views.Count > 0)
                _debugLogger.LogInformation("[RTV.NominatePanel] Closing {Count} open panel(s).", _views.Count);

            Destroy();
        }

        private bool EnsureHud(out CCSCustomHudLayout hud)
        {
            if (_hud is { IsValid: true } existing)
            {
                hud = existing;
                return true;
            }

            hud = null!;
            string layout = _panoramaConfig.AddonName?.Trim() ?? "";
            if (layout.Length == 0)
            {
                _logger.LogWarning("[RTV.NominatePanel] PanoramaMenu.AddonName is empty; no panel will be shown.");
                return false;
            }

            var spawned = CustomHud.SpawnLayout(layout, "rtv_panorama_nominate", _logger);
            if (spawned == null)
                return false;

            // Same layout as the vote panel: keep that part hidden on this entity
            spawned.SetHasClass(VoteDialogPanel, VoteDialogHiddenClass, true);

            _hud = hud = spawned;
            _appliedImages.Clear();
            _debugLogger.LogInformation("[RTV.NominatePanel] Spawned custom_hud_layout #{Index} with layout '{Layout}'.", spawned.Index, layout);
            return true;
        }

        private void Render(CCSCustomHudLayout hud, CCSPlayerController player, View view)
        {
            string header = _localizer.Localize(view.IsSearch ? "nominate.multiple-maps" : "nominate.title");
            SetText(hud, player, HeaderPanel, "rtv_nom_header", header);
            SetText(hud, player, PagePanel, "rtv_nom_page", $"{view.Page + 1} / {view.PageCount}");

            var mine = _nominations.NominationsOf(player.Slot);
            if (!_appliedImages.TryGetValue(player.Slot, out var applied))
                _appliedImages[player.Slot] = applied = new string?[PageSize];

            for (int i = 0; i < PageSize; i++)
            {
                int card = i + 1;
                int index = view.Page * PageSize + i;
                bool hasMap = index < view.Maps.Count;
                // Empty slots stay in the layout (transparent) so the panel keeps its size
                hud.SetHasClassForPlayer(player, $"{CardPrefix}{card}", EmptyClass, !hasMap);
                if (!hasMap)
                {
                    SetText(hud, player, $"{NamePrefix}{card}", $"rtv_nom_name_{card}", "");
                    continue;
                }

                var map = view.Maps[index];
                SetText(hud, player, $"{NamePrefix}{card}", $"rtv_nom_name_{card}", map.Name);

                string imageClass = CustomHud.MapImageClass(map);
                if (applied[i] is { } previous && previous != imageClass)
                    hud.SetHasClassForPlayer(player, $"{ImagePrefix}{card}", previous, false);
                hud.SetHasClassForPlayer(player, $"{ImagePrefix}{card}", imageClass, true);
                hud.SetHasClassForPlayer(player, $"{ImagePrefix}{card}", ImageOnClass, true);
                applied[i] = imageClass;

                hud.SetHasClassForPlayer(player, $"{CardPrefix}{card}", DisabledClass, _mapCooldown.IsMapInCooldown(map.Name));
                hud.SetHasClassForPlayer(player, $"{CardPrefix}{card}", MineClass,
                    mine.Contains(map.Name, StringComparer.OrdinalIgnoreCase));
            }

            hud.SetHasClassForPlayer(player, PrevPanel, ButtonOffClass, view.Page == 0);
            hud.SetHasClassForPlayer(player, NextPanel, ButtonOffClass, view.Page >= view.PageCount - 1);
        }

        // Written against the dialog and the label, like CustomHud: the client's
        // dialog-variable scope resolution is not pinned down.
        private static void SetText(CCSCustomHudLayout hud, CCSPlayerController player, string panel, string variable, string value)
        {
            hud.SetDialogVariableStringForPlayer(player, DialogPanel, variable, value);
            hud.SetDialogVariableStringForPlayer(player, panel, variable, value);
        }

        private void OnHudClicked(CCSPlayerController player, CCSCustomHudLayout customLayout, string buttonId)
        {
            if (_hud is not { IsValid: true } hud || customLayout.Handle != hud.Handle)
                return;

            int slot = player.Slot;
            Server.NextFrame(() => HandleClick(slot, buttonId));
        }

        private void HandleClick(int slot, string buttonId)
        {
            try
            {
                var player = Utilities.GetPlayerFromSlot(slot);
                if (player is null || !player.IsValid || _hud is not { IsValid: true } hud || !_views.TryGetValue(slot, out var view))
                    return;

                _debugLogger.LogInformation("[RTV.NominatePanel] Click: button='{Button}' slot={Slot} page={Page}.", buttonId, slot, view.Page);

                if (buttonId == ClosePanel)
                {
                    Close(player);
                    return;
                }

                if (buttonId == PrevPanel || buttonId == NextPanel)
                {
                    int page = Math.Clamp(view.Page + (buttonId == NextPanel ? 1 : -1), 0, view.PageCount - 1);
                    if (page != view.Page)
                    {
                        view.Page = page;
                        Render(hud, player, view);
                    }
                    return;
                }

                if (buttonId.StartsWith(CardPrefix, StringComparison.Ordinal)
                    && int.TryParse(buttonId.AsSpan(CardPrefix.Length), out int card)
                    && card >= 1 && card <= PageSize)
                {
                    int index = view.Page * PageSize + card - 1;
                    if (index >= view.Maps.Count)
                        return;

                    // Nominating may have been switched off since the panel opened
                    if (_pluginState.DisableCommands || _pluginState.EofVoteHappening || !_nomConfig.Enabled)
                    {
                        player.PrintToChat(_localizer.LocalizeWithPrefix("general.validation.disabled"));
                        Close(player);
                        return;
                    }

                    if (!_nominations.Nominate(player, view.Maps[index].Name))
                        return;

                    int limit = _nomConfig.NominateLimit;
                    if (limit > 0 && _nominations.NominationsOf(slot).Count >= limit)
                        Close(player);
                    else
                        Render(hud, player, view);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[RTV.NominatePanel] Failed to handle click '{Button}' for slot {Slot}.", buttonId, slot);
            }
        }

        private HookResult OnPlayerDisconnect(EventPlayerDisconnect @event, GameEventInfo info)
        {
            if (@event.Userid is { IsValid: true } player)
                Close(player);

            return HookResult.Continue;
        }

        private void Destroy()
        {
            var hud = _hud;
            _hud = null;
            var slots = _views.Keys.ToList();
            _views.Clear();
            _appliedImages.Clear();

            if (hud is not { IsValid: true })
                return;

            try
            {
                // Release every cursor before the entity disappears
                foreach (int slot in slots)
                {
                    var player = Utilities.GetPlayerFromSlot(slot);
                    if (player is { IsValid: true })
                        hud.SetInputCaptureEnabled(player, false);
                }

                hud.AcceptInput("Kill");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "[RTV.NominatePanel] Failed to kill the nominate panel entity.");
            }
        }
    }
}
