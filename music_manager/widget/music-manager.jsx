// music-manager.jsx — widget Übersicht pour Music Manager
//
// Recherche Deezer (morceaux et playlists), aperçu et import, dépôt de CSV
// Exportify, suivi d'import en direct et lecture Apple Music.
//
// Prérequis : l'app installée via le DMG (binaire ~/.local/bin/music-manager).
// Installation : déposer ce fichier dans
//   ~/Library/Application Support/Übersicht/widgets/
//
// Dépôt : https://github.com/ThomasDumont01/apple-music-manager

import { React, run } from "uebersicht"
const { useState, useEffect, useRef } = React

// ── Binaire ────────────────────────────────────────────────────────────────
// Le DMG installe via `uv tool install`, donc ~/.local/bin/music-manager.
// On retombe sur le PATH si l'utilisateur l'a installé autrement. Chaque
// run() ouvre son propre shell, d'où le préfixe répété plutôt qu'un export.
const MM_RUN =
  `MM="$HOME/.local/bin/music-manager"; ` +
  `[ -x "$MM" ] || MM=music-manager; "$MM"`

const ACCENT = "#007aff"
const ACCENT_AMBER = "#e8852f"
const SUCCESS = "#34c759"
const DANGER = "#ff3b30"

// ── Shell command ──────────────────────────────────────────────────────────
export const command =
  `echo "===MUSIC_STATUS==="; ${MM_RUN} import-status 2>/dev/null || echo "{}"; ` +
  `echo "===MUSIC_HOME==="; ${MM_RUN} home --recent-limit 5 2>/dev/null || echo "{}"`

export const refreshFrequency = 15000  // 15 s — réactif aux changements Apple Music

// ── Styles ─────────────────────────────────────────────────────────────────
export const className = `
  top: 15px; left: 15px; width: 340px;
  max-height: 85vh;
  display: flex; flex-direction: column;
  font-family: -apple-system, 'SF Pro Text', system-ui, sans-serif;
  color: #1c1c1e;
  /* Glassmorphism : fond translucide + flou backdrop */
  background: rgba(245, 245, 247, 0.82);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border-radius: 20px;
  border: 0.5px solid rgba(255,255,255,0.6);
  box-shadow:
    0 14px 56px rgba(0,0,0,0.28),
    0 2px 8px rgba(0,0,0,0.08),
    inset 0 1px 0 rgba(255,255,255,0.5);
  overflow: hidden;

  & > div { display: flex; flex-direction: column; max-height: 85vh; }

  .dashboard-drag { cursor: grab; }
  .dashboard-drag:active { cursor: grabbing; }

  /* ── Animations ───────────────────────────────────── */
  @keyframes rise {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes pulseSoft {
    0% { box-shadow: 0 0 0 0 rgba(232,133,47,0.45); }
    70% { box-shadow: 0 0 0 6px rgba(232,133,47,0); }
    100% { box-shadow: 0 0 0 0 rgba(232,133,47,0); }
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  /* ── Topbar + onglets de tête ───────────────────── */
  .topbar {
    flex: 0 0 auto;
    padding: 16px 18px 12px;
    border-bottom: 0.5px solid rgba(0,0,0,0.06);
    background: rgba(255,255,255,0.25);
    border-radius: 20px 20px 0 0;
  }
  .tabsmain {
    display: flex; gap: 4px;
    background: rgba(120,120,128,0.14);
    border-radius: 11px; padding: 3px;
  }
  .tabmain {
    flex: 1; text-align: center;
    font-size: 12px; font-weight: 700;
    padding: 7px 0;
    border-radius: 8px;
    color: #6e6e73;
    cursor: pointer; user-select: none;
    display: flex; align-items: center; justify-content: center; gap: 5px;
    transition: color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease, transform 0.12s ease;
  }
  .tabmain:hover { color: #1c1c1e; }
  .tabmain:active { transform: scale(0.96); }
  .tabmain.active {
    background: #fff;
    color: #1c1c1e;
    box-shadow: 0 1px 3px rgba(0,0,0,0.12), 0 0 0 0.5px rgba(0,0,0,0.04);
  }
  .tabico { font-size: 13px; line-height: 1; filter: grayscale(0.2); }

  /* ── Scroll container commun ──────────────────── */
  .scroll {
    flex: 1 1 auto; min-height: 0;
    max-height: calc(85vh - 80px);
    overflow-y: auto;
    padding: 12px 18px 16px;
    animation: fadeIn 0.28s ease both;
  }
  .scroll::-webkit-scrollbar { width: 6px; }
  .scroll::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.22); border-radius: 3px; }
  .scroll::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.38); }
  .scroll::-webkit-scrollbar-track { background: transparent; }

  .empty { font-size: 12.5px; color: #8a8a8e; font-style: italic; padding: 16px 0; text-align: center; animation: fadeIn 0.25s ease both; }
  .sectitle {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; color: #6e6e73;
    margin: 4px 0 8px;
    animation: fadeIn 0.3s ease both;
  }
  .sectitle:not(:first-child) { margin-top: 18px; }

  /* Animation stagger sur les items des listes — appliquée au container */
  .anim > .row, .anim > .plcell {
    opacity: 0; animation: rise 0.36s ease forwards;
  }
  .anim > *:nth-child(1) { animation-delay: 0.02s; }
  .anim > *:nth-child(2) { animation-delay: 0.05s; }
  .anim > *:nth-child(3) { animation-delay: 0.08s; }
  .anim > *:nth-child(4) { animation-delay: 0.11s; }
  .anim > *:nth-child(5) { animation-delay: 0.14s; }
  .anim > *:nth-child(6) { animation-delay: 0.17s; }
  .anim > *:nth-child(7) { animation-delay: 0.20s; }
  .anim > *:nth-child(8) { animation-delay: 0.23s; }
  .anim > *:nth-child(9) { animation-delay: 0.26s; }
  .anim > *:nth-child(n+10) { animation-delay: 0.28s; }

  /* Colonne de texte flexible d'une ligne de piste. Définie côté
     dashboard dans la section Tâches, d'où son absence à l'extraction :
     sans elle les titres ne se tronquent plus et la ligne déborde. */
  .body { flex: 1; min-width: 0; }

  /* ── Music ─────────────────────────────────── */
  .searchbox {
    display: flex; align-items: center; gap: 9px;
    background: #fff;
    border: 0.5px solid rgba(0,0,0,0.1);
    border-radius: 11px;
    padding: 8px 12px;
    margin-bottom: 12px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }
  .searchbox:focus-within {
    border-color: rgba(0,122,255,0.6);
    box-shadow: 0 0 0 3px rgba(0,122,255,0.12);
  }
  .searchbox:focus-within svg.ico { stroke: ${ACCENT}; transform: scale(1.05); }
  .searchbox svg.ico { transition: transform 0.18s ease; }
  .searchbox svg.ico { width: 15px; height: 15px; stroke: ${ACCENT}; stroke-width: 2.2; fill: none; flex: 0 0 auto; }
  .searchbox input { flex: 1; border: none; outline: none; background: transparent; font-size: 13px; color: #1c1c1e; }
  .searchbox input::placeholder { color: #aab2bd; }
  .clearbtn { width: 18px; height: 18px; border-radius: 50%; background: rgba(120,120,128,0.18); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 11px; line-height: 1; cursor: pointer; user-select: none; }

  /* Toggle mode recherche : morceaux ↔ playlists Deezer */
  .modepills {
    display: flex; gap: 6px;
    margin: -6px 0 12px;
  }
  .modepill {
    flex: 1;
    padding: 6px 0;
    border-radius: 8px;
    text-align: center;
    font-size: 11px; font-weight: 700;
    color: #6e6e73;
    background: rgba(120,120,128,0.12);
    cursor: pointer; user-select: none;
    transition: background 0.12s, color 0.12s;
  }
  .modepill:hover { background: rgba(120,120,128,0.2); }
  .modepill.active { background: ${ACCENT}; color: #fff; }

  /* Variante 2 colonnes pour la recherche playlists Deezer (place au creator). */
  .plgrid-search {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 14px;
    margin-bottom: 8px;
    width: 100%;
  }
  .plgrid-search .plcover {
    position: relative;
    border-radius: 12px;
    overflow: hidden;
  }
  .plbadge {
    position: absolute; top: 6px; right: 6px;
    padding: 2px 8px;
    border-radius: 999px;
    background: rgba(0,0,0,0.55);
    color: #fff;
    font-size: 9.5px; font-weight: 700;
    letter-spacing: 0.02em;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    z-index: 2;
  }
  .ploverlay {
    position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(0,0,0,0) 45%, rgba(0,0,0,0.8));
    display: flex; align-items: flex-end; justify-content: center;
    padding-bottom: 10px;
    opacity: 0;
    transition: opacity 0.18s ease;
    font-size: 11px; font-weight: 700; color: #fff;
    pointer-events: none;
  }
  .plgrid-search .plcell:hover .ploverlay { opacity: 1; }

  /* Home : pas d'overlay au hover (le lift + shadow suffisent). L'overlay
     n'apparaît qu'en état loading (rendu conditionnel côté React). */
  .plgrid .ploverlay {
    background: rgba(0,0,0,0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    font-size: 16px;
    letter-spacing: 0.04em;
    font-weight: 600;
    color: rgba(255,255,255,0.95);
    border-radius: 10px;
    opacity: 1;  /* rendu uniquement quand loading → toujours visible */
  }
  .plgrid-search .plcaption { font-size: 12px; margin-top: 7px; }
  .plsub {
    font-size: 10.5px; color: #8a8a8e;
    text-align: center;
    margin-top: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .plcell.loading { opacity: 0.65; cursor: progress; pointer-events: none; }
  .plcell.loading .ploverlay { opacity: 1; background: rgba(0,0,0,0.5); }

  /* Mode Exportify — bandeau + grille de CSVs détectés */
  .expobanner {
    display: flex; gap: 8px; align-items: center;
    margin-bottom: 12px;
    padding: 10px 12px;
    background: rgba(120,120,128,0.08);
    border-radius: 10px;
  }
  .expobtn {
    background: linear-gradient(135deg, #1db954 0%, #168f3e 100%);
    color: #fff;
    padding: 6px 12px;
    border-radius: 8px;
    font-weight: 700;
    font-size: 11.5px;
    cursor: pointer; user-select: none;
    box-shadow: 0 1px 4px rgba(29,185,84,0.25);
    transition: transform 0.12s;
    flex: 0 0 auto;
  }
  .expobtn:hover { transform: translateY(-1px); }
  .expobtn:active { transform: scale(0.97); }
  .expohint {
    flex: 1;
    font-size: 10.5px; color: #6e6e73;
    line-height: 1.4;
  }
  .modepills.subtle {
    margin: 6px 0 10px;
    gap: 5px;
  }
  .modepills.subtle .modepill {
    padding: 5px 0;
    font-size: 10.5px;
    border-radius: 7px;
  }
  .dropzone {
    border: 2px dashed rgba(120,120,128,0.32);
    border-radius: 13px;
    padding: 28px 14px 22px;
    text-align: center;
    font-size: 12.5px;
    color: #4a4a4f;
    cursor: pointer;
    user-select: none;
    transition: border-color 0.18s, background 0.18s, transform 0.12s;
    margin: 0 0 10px;
  }
  .dropzone:hover, .dropzone.active {
    border-color: ${ACCENT};
    background: rgba(0,122,255,0.06);
    color: ${ACCENT};
  }
  .dropzone.active {
    transform: scale(1.015);
    animation: dropPulse 1.4s ease-in-out infinite;
  }
  @keyframes dropPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(0,122,255,0); }
    50% { box-shadow: 0 0 0 5px rgba(0,122,255,0.18); }
  }
  .dropzone.disabled {
    opacity: 0.7;
    cursor: progress;
    border-style: solid;
  }
  .dropicon {
    font-size: 26px;
    color: ${ACCENT};
    margin-bottom: 6px;
    line-height: 1;
  }
  .dropsub {
    font-size: 10.5px;
    color: #8a8a8e;
    margin-top: 4px;
  }
  .pheadactions {
    display: flex; gap: 6px;
    flex: 0 0 auto;
    margin-left: 6px;
  }
  .pheadbtn {
    transition: transform 0.12s ease, background 0.15s;
  }
  .pheadbtn:hover { transform: scale(1.1); }
  .pheadbtn:active { transform: scale(0.92); }
  /* Bouton principal d'import direct dans le header — visible sans scroll. */
  .ibtn.pheadbtn.primary {
    background: ${ACCENT};
    color: #fff;
    font-size: 14px; font-weight: 800;
    box-shadow: 0 1px 4px rgba(0,122,255,0.35);
  }
  .ibtn.pheadbtn.primary:hover { background: #0068d6; }
  /* Bouton "importer la sélection" quand des morceaux sont cochés. */
  .ibtn.pheadbtn.added {
    background: ${SUCCESS};
    color: #fff;
    font-size: 11px; font-weight: 800;
    padding: 0 8px;
    width: auto; min-width: 30px;
    border-radius: 13px;
  }

  /* Barre d'import de PlaylistPreview — sticky en bas pour rester
     visible même quand la liste est longue (radios de 25+ titres). */
  .pimport-bar {
    position: sticky;
    bottom: 0;
    margin: 8px -4px 0;
    padding: 8px 4px 4px;
    background: linear-gradient(180deg, rgba(245,245,247,0) 0%, rgba(245,245,247,0.92) 30%, rgba(245,245,247,0.98) 100%);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    z-index: 5;
  }

  /* Le header de la preview reste toujours accessible aux clics — z-index
     supérieur au sticky bar pour éviter tout chevauchement lors du scroll. */
  .phead { position: relative; z-index: 6; }
  .pheadactions { position: relative; z-index: 7; pointer-events: auto; }

  /* Bouton "···" en haut à droite d'une carte playlist du home — révèle la
     "Radio de cette playlist" (seed = top tracks de la playlist locale). */
  .plcell { position: relative; }
  .plcell:hover .plmore { opacity: 1; }

  /* Fade-in pour les messages d'état (loader, erreur, empty) */
  .empty { animation: fadeIn 0.22s ease both; }

  /* Transitions plus douces sur les éléments interactifs */
  .modepill { transition: background 0.18s, color 0.18s, transform 0.1s; }
  .modepill:active { transform: scale(0.97); }
  .importbtn { transition: transform 0.12s, box-shadow 0.18s; }
  .importbtn:active { transform: scale(0.98); }

  /* ── Preview playlist (avant import) ────────────── */
  .preview { animation: fadeIn 0.22s ease both; }
  .phead {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 0.5px solid rgba(0,0,0,0.08);
  }
  .pback {
    flex: 0 0 auto;
    width: 28px; height: 28px;
    border-radius: 50%;
    background: rgba(120,120,128,0.16);
    color: #1c1c1e;
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; font-weight: 700;
    cursor: pointer; user-select: none;
    transition: background 0.15s, transform 0.1s;
  }
  .pback:hover { background: rgba(120,120,128,0.28); }
  .pback:active { transform: scale(0.92); }
  .phcover {
    flex: 0 0 auto;
    width: 48px; height: 48px;
    border-radius: 8px;
    background-size: cover; background-position: center;
    background-color: rgba(0,0,0,0.06);
    box-shadow: 0 2px 6px rgba(0,0,0,0.14);
    display: flex; align-items: center; justify-content: center;
    color: ${ACCENT}; font-size: 22px;
  }
  .phcover.cover-mosaic,
  .plcover.cover-mosaic {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    grid-template-rows: repeat(2, 1fr);
    gap: 0;
    padding: 0;
    overflow: hidden;
    background-image: none !important;
    color: transparent;
  }
  .cover-tile {
    width: 100%; height: 100%;
    background-size: cover;
    background-position: center;
    background-color: rgba(0,0,0,0.06);
  }
  .phbody { flex: 1; min-width: 0; }
  .phname {
    font-size: 13.5px; font-weight: 700;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .phmeta {
    font-size: 10.5px; color: #6e6e73; margin-top: 2px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  /* Liste des titres : variante compacte de .row */
  .plist { margin-bottom: 8px; }
  .prow {
    display: flex; align-items: center; gap: 9px;
    padding: 5px 4px;
    border-radius: 8px;
    transition: background 0.15s ease;
  }
  .prow:hover { background: rgba(0,0,0,0.04); }
  .prow .cover { width: 34px; height: 34px; }
  .prow .body { flex: 1; min-width: 0; }
  .prow .tname { font-size: 12px; font-weight: 600; }
  .prow .tartist { font-size: 10.5px; margin-top: 0; }
  .prow .ibtn { width: 24px; height: 24px; font-size: 12px; }
  /* Stagger anim pour les preview rows (s'ajoute à .anim > .row déjà défini) */
  .anim > .prow {
    opacity: 0; animation: rise 0.36s ease forwards;
  }

  .plgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 8px; }
  .plcell {
    cursor: pointer; user-select: none;
    transition: transform 0.18s cubic-bezier(0.34, 1.56, 0.64, 1);
    /* Sans min-width: 0, un .plsub long (nowrap) force la cell à s'étendre
       au-delà de 1fr — col1 devient géante. Cf. règle CSS Grid : min-width
       auto = min-content sur les items, on doit forcer 0 pour que ellipsis
       fonctionne. */
    min-width: 0;
  }
  .plcell:hover { transform: translateY(-3px); }
  .plcell:active { transform: scale(0.96); }
  .plcover {
    width: 100%; aspect-ratio: 1;
    border-radius: 10px;
    background-size: cover; background-position: center;
    background-color: rgba(0,0,0,0.06);
    box-shadow: 0 2px 6px rgba(0,0,0,0.12);
    display: flex; align-items: center; justify-content: center;
    font-size: 32px; color: ${ACCENT};
    transition: box-shadow 0.2s ease;
  }
  .plcell:hover .plcover { box-shadow: 0 6px 18px rgba(0,0,0,0.22); }
  .plcaption {
    margin-top: 6px;
    font-size: 11.5px; font-weight: 600;
    text-align: center;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    color: #1c1c1e;
  }

  .row {
    display: flex; align-items: center; gap: 10px;
    padding: 6px 6px;
    border-radius: 9px;
    cursor: pointer;
    transition: background 0.18s ease, transform 0.12s ease;
  }
  .row:hover { background: rgba(0,0,0,0.04); transform: translateX(2px); }
  .cover { width: 38px; height: 38px; border-radius: 6px; background-size: cover; background-position: center; background-color: rgba(0,0,0,0.06); flex: 0 0 auto; }
  .tname { font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tartist { font-size: 11px; color: #6e6e73; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
  .actions { display: flex; gap: 4px; flex: 0 0 auto; }
  .ibtn { width: 26px; height: 26px; border-radius: 50%; background: rgba(120,120,128,0.14); color: #1c1c1e; display: flex; align-items: center; justify-content: center; font-size: 13px; cursor: pointer; user-select: none; transition: background 0.15s, color 0.15s, transform 0.1s; }
  .ibtn:hover { background: rgba(120,120,128,0.24); }
  .ibtn:active { transform: scale(0.93); }
  .ibtn.playing { background: ${ACCENT}; color: #fff; }
  .ibtn.added { background: ${SUCCESS}; color: #fff; }
  .ibtn.inlib { background: rgba(52,199,89,0.15); color: ${SUCCESS}; font-size: 12px; }
  .ibtn.inlib:hover { background: rgba(52,199,89,0.28); }
  .importbtn {
    width: 100%; padding: 10px 0;
    border-radius: 11px;
    background: linear-gradient(135deg, ${ACCENT}, #4a9eff);
    color: #fff;
    font-size: 13px; font-weight: 700;
    text-align: center;
    cursor: pointer; user-select: none;
    margin-top: 8px;
    box-shadow: 0 2px 8px rgba(0,122,255,0.3);
    transition: transform 0.12s ease, box-shadow 0.18s ease, background 0.2s ease;
  }
  .importbtn:hover {
    background: linear-gradient(135deg, #0062cc, ${ACCENT});
    box-shadow: 0 4px 14px rgba(0,122,255,0.45);
    transform: translateY(-1px);
  }
  .importbtn:active { transform: scale(0.98); }
  .importbtn.disabled { background: rgba(120,120,128,0.3); cursor: not-allowed; box-shadow: none; transform: none; }

  /* Loader pour la recherche music */
  .loader {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid rgba(0,122,255,0.2);
    border-top-color: ${ACCENT};
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    vertical-align: -2px;
    margin-right: 6px;
  }

  /* Icon button click feedback */
  .ibtn { transition: background 0.15s ease, color 0.15s ease, transform 0.1s ease, box-shadow 0.15s ease; }
  .ibtn:hover { box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
  .ibtn.playing { animation: pulseSoft 1.8s ease-in-out infinite; }

  /* ── Import progress & done ───────────────────── */
  .iblock {
    background: #fff;
    border: 0.5px solid rgba(0,0,0,0.1);
    border-radius: 11px;
    padding: 12px 14px;
    margin-top: 12px;
    animation: fadeIn 0.25s ease both;
  }
  .iblock .ptitle { font-size: 12.5px; font-weight: 700; color: #1c1c1e; }
  .iblock .psub {
    font-size: 11px; color: #6e6e73; margin-top: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .iblock .pbar {
    height: 5px; border-radius: 3px;
    background: rgba(0,0,0,0.1);
    overflow: hidden;
    margin-top: 9px;
  }
  .iblock .pfill {
    height: 100%;
    background: linear-gradient(90deg, ${ACCENT}, #4aa8ff);
    border-radius: 3px;
    transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
  }
  .iblock .psub.warn {
    color: ${ACCENT_AMBER};
    white-space: normal;
  }
  .iblock .psub.warn code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10.5px;
    background: rgba(232,133,47,0.12);
    padding: 1px 4px; border-radius: 4px;
  }
  .iblock .pfill.waiting {
    background: linear-gradient(90deg, ${ACCENT_AMBER}, #f5b26b);
  }
  .iblock .pbadge { font-size: 11.5px; font-weight: 700; color: ${SUCCESS}; }
  .iblock .pbadge.fail { color: ${DANGER}; margin-left: 10px; }
  .iblock .pbadge.skip { color: #8a8a8e; margin-left: 10px; }
  .iblock .pdismiss {
    position: absolute; top: 8px; right: 12px;
    font-size: 14px; color: #8a8a8e; line-height: 1;
    cursor: pointer;
  }
  .iblock { position: relative; }

  /* Détail des échecs : sans ça l'utilisateur ne savait ni quels morceaux
     avaient échoué ni pourquoi. */
  .ifails { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
  .ifail {
    display: flex; align-items: baseline; gap: 8px;
    font-size: 10.5px; line-height: 1.35;
  }
  .ifail-name {
    flex: 1 1 auto; color: #1c1c1e;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .ifail-why { flex: 0 0 auto; color: #8a8a8e; }
  .ifail.more { color: #8a8a8e; justify-content: flex-start; }
  .iretry {
    margin-top: 10px;
    text-align: center;
    padding: 6px 10px;
    border-radius: 8px;
    background: rgba(1,120,212,0.10);
    color: ${ACCENT};
    font-size: 11.5px; font-weight: 700;
    cursor: pointer; user-select: none;
    transition: background 0.15s;
  }
  .iretry:hover { background: rgba(1,120,212,0.18); }
  .iretry.disabled { opacity: 0.5; pointer-events: none; }
  .iblock .ihead {
    display: flex; align-items: center; gap: 8px;
    justify-content: space-between;
  }
  .icancel {
    flex: 0 0 auto;
    width: 22px; height: 22px;
    border-radius: 50%;
    background: rgba(255,59,48,0.12);
    color: ${DANGER};
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 700;
    cursor: pointer; user-select: none;
    transition: background 0.15s, transform 0.12s;
  }
  .icancel:hover { background: rgba(255,59,48,0.22); transform: scale(1.08); }
  .icancel:active { transform: scale(0.92); }
  .icancel.disabled { opacity: 0.4; pointer-events: none; }

  /* Barre sticky pour la progression d'import : reste en bas du music panel
     pendant que l'utilisateur navigue sur le reste du widget. */
  .import-sticky {
    position: sticky;
    bottom: -1px;
    background: transparent;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    margin: 12px -8px -1px;
    padding: 0 8px 1px;
    border-top: none;
    z-index: 20;
  }
  .import-sticky .iblock {
    background: rgba(231,234,239,0.92);
    margin-top: 8px;
    margin-bottom: 8px;
  }

  /* Bouton "voir plus" / "réduire" sous la grille de playlists */
  .seemore {
    text-align: center;
    margin: 4px 0 12px;
    padding: 5px 12px;
    font-size: 11px; font-weight: 600;
    color: ${ACCENT};
    background: rgba(0,122,255,0.07);
    border-radius: 8px;
    cursor: pointer; user-select: none;
    transition: background 0.15s;
  }
  .seemore:hover { background: rgba(0,122,255,0.14); }

  .blocked-music {
    background: #fff5f5;
    border: 0.5px solid #ffd5d5;
    color: ${DANGER};
    border-radius: 11px;
    padding: 12px 14px;
    margin-top: 12px;
    font-size: 12px; font-weight: 600;
    animation: fadeIn 0.25s ease both;
  }
  .mix-cell.loading { opacity: 0.6; cursor: progress; pointer-events: none; }
  .reco-card-actions .ibtn {
    width: 26px; height: 26px;
    background: rgba(255,255,255,0.92);
    color: #1c1c1e;
    font-size: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  }
  .reco-card-actions .ibtn.playing { background: ${ACCENT}; color: #fff; }
  .reco-card-actions .ibtn.added { background: ${SUCCESS}; color: #fff; }
  .reco-card-actions .ibtn.inlib { background: rgba(52,199,89,0.85); color: #fff; }

  /* Compactage des actions dans les lignes de recherche/récents :
     3 boutons alignés (▶ ↻ +) avec un gap serré pour ne pas étirer la row. */
  .row .actions {
    display: flex; align-items: center; gap: 4px;
    flex: 0 0 auto;
  }
  .row .actions .ibtn {
    width: 24px; height: 24px;
    font-size: 12px;
  }
`

// ── Drag handle (pattern tasks.jsx, fonctionne) ─────────────────────────────
let dragInit = false
function findRoot() {
  const h = document.querySelector(".dashboard-drag")
  if (!h) return null
  let el = h
  while (el && el.parentElement) {
    el = el.parentElement
    const p = window.getComputedStyle(el).position
    if (p === "absolute" || p === "fixed") return el
  }
  return null
}

// Drag éphémère : la position bouge en session via inline styles, mais n'est
// PAS persistée. À chaque refresh du widget, la position CSS par défaut
// (top:15px left:15px) reprend la main.
function bindDrag() {
  setTimeout(() => {
    const root = findRoot()
    const handle = document.querySelector(".dashboard-drag")
    if (!root || !handle) return
    if (handle.dataset.dragBound) return
    handle.dataset.dragBound = "1"
    dragInit = true
    let sx, sy, sl, st, dg = false
    const dn = (e) => {
      // Les clics sur input/onglets/icônes ne déclenchent pas le drag.
      if (e.target.closest("input, label, button, .tabmain, .clearbtn")) return
      dg = true
      const r = root.getBoundingClientRect()
      sx = e.clientX; sy = e.clientY; sl = r.left; st = r.top
      root.style.left = sl + "px"; root.style.top = st + "px"
      root.style.right = "auto"; root.style.bottom = "auto"
      root.style.transition = "none"
      e.preventDefault(); e.stopPropagation()
      window.addEventListener("mousemove", mv, true)
      window.addEventListener("mouseup", up, true)
    }
    const mv = (e) => {
      if (!dg) return
      root.style.left = Math.max(0, sl + (e.clientX - sx)) + "px"
      root.style.top = Math.max(0, st + (e.clientY - sy)) + "px"
      e.preventDefault()
    }
    const up = () => {
      if (!dg) return
      dg = false
      window.removeEventListener("mousemove", mv, true)
      window.removeEventListener("mouseup", up, true)
      // Pas de persistance : la position est temporaire jusqu'au refresh.
    }
    handle.addEventListener("mousedown", dn, true)
  }, 80)
}


// ── Helpers communs ────────────────────────────────────────────────────────
function escapeShellArg(s) {
  return "'" + String(s).replace(/'/g, "'\\''") + "'"
}

function trackCoverUrl(t) {
  if (!t) return ""
  return t.cover_url || t.cover_thumb || t.artwork_url || ""
}

function firstMosaicCovers(tracks) {
  if (!Array.isArray(tracks)) return []
  return tracks.map(trackCoverUrl).filter(Boolean).slice(0, 4)
}

function playlistMosaicUrls(p) {
  const files = p && Array.isArray(p.mosaic_cover_filenames)
    ? p.mosaic_cover_filenames
    : []
  return files.filter(Boolean).slice(0, 4).map(f => `music-manager.assets/${f}`)
}

function PlaylistArtwork({className, imageUrl, covers, fallback = "♫", children}) {
  if (imageUrl) {
    return (
      <div className={className} style={{backgroundImage: `url("${imageUrl}")`}}>
        {children}
      </div>
    )
  }
  const tiles = Array.isArray(covers) ? covers.filter(Boolean).slice(0, 4) : []
  if (tiles.length > 0) {
    return (
      <div className={`${className} cover-mosaic`}>
        {[0, 1, 2, 3].map(i => (
          <div
            className="cover-tile"
            key={i}
            style={{backgroundImage: tiles[i] ? `url("${tiles[i]}")` : "none"}}
          />
        ))}
        {children}
      </div>
    )
  }
  return <div className={className}>{fallback}{children}</div>
}


// ── MUSIC panel ────────────────────────────────────────────────────────────

// Chaque cause d'échec a son message. Avant, tout arrivait ici sous le seul
// mot "youtube_failed" : impossible de distinguer un morceau absent de
// YouTube d'un yt-dlp périmé, et impossible de savoir quoi faire.
const IMPORT_REASONS = {
  youtube_not_found: "Introuvable sur YouTube",
  youtube_blocked: "YouTube a refusé le téléchargement",
  youtube_unavailable: "Vidéo supprimée ou indisponible ici",
  youtube_rate_limited: "YouTube limite les téléchargements",
  youtube_cookies_needed: "Vérification YouTube à faire dans l'app",
  youtube_timeout: "Téléchargement trop long",
  youtube_error: "Échec du téléchargement",
  youtube_failed: "Échec du téléchargement",
  not_on_deezer: "Introuvable sur Deezer",
  duration_suspect: "Durée inattendue — à vérifier dans l'app",
  apple_import_failed: "Apple Music a refusé le fichier",
  import_error: "Erreur pendant l'import",
  no_apple_id: "Apple Music n'a pas répondu",
  recently_failed: "Déjà tenté il y a moins de 10 min",
}

function importReasonLabel(entry) {
  if (!entry) return "Échec"
  return (
    IMPORT_REASONS[entry.detail] ||
    IMPORT_REASONS[entry.reason] ||
    "Échec"
  )
}

function importEntryLabel(entry) {
  if (!entry) return ""
  const name = [entry.artist, entry.title].filter(Boolean).join(" — ")
  return name || entry.isrc || ""
}

function ImportProgress({status, onCancel, cancelling}) {
  const total = status.total || 0
  const current = status.current || 0
  const pct = total ? Math.round(100 * current / total) : 0
  // Le worker dort (backoff YouTube). Sans ça la barre restait figée sans
  // explication pendant parfois plusieurs minutes.
  const waiting = Number(status.waiting_seconds) > 0
  return (
    <div className="iblock">
      <div className="ihead">
        <div className="ptitle">
          {cancelling ? "Annulation…" : `Import ${current}/${total}…`}
        </div>
        {onCancel && status.cancellable !== false && (
          <span
            className={"icancel " + (cancelling ? "disabled" : "")}
            onClick={cancelling ? undefined : onCancel}
            title="Annuler l'import"
          >✕</span>
        )}
      </div>
      {waiting ? (
        <div className="psub warn">
          YouTube limite les téléchargements — reprise dans {status.waiting_seconds}s
        </div>
      ) : (
        status.current_title && <div className="psub">{status.current_title}</div>
      )}
      <div className="pbar"><div className={"pfill" + (waiting ? " waiting" : "")} style={{width: pct + "%"}}/></div>
    </div>
  )
}

function ImportDone({status, onDismiss, onRetry, retrying}) {
  const done = status.completed || []
  const failed = status.failed || []
  const skipped = status.skipped || []
  const ok = done.length
  const ko = failed.length
  // status.error is set by the widget itself when the polling timeout fires
  // because the worker --detach never wrote widget_status.json (cold-start
  // crash, missing venv, etc.). Surface it so the user knows why.
  const topLevelError = !ok && !ko && status.error ? String(status.error) : ""
  const retryIsrcs = [...failed, ...skipped]
    .map(entry => entry.isrc)
    .filter(Boolean)
  // Un yt-dlp périmé fait échouer tout un lot en "YouTube a refusé" : c'est
  // la cause n°1 et elle se corrige en une commande.
  const staleTool = status.yt_dlp_stale &&
    failed.some(entry => entry.detail === "youtube_blocked")
  return (
    <div className="iblock">
      <span className="pdismiss" onClick={onDismiss} title="Fermer">×</span>
      <div className="ptitle">
        <span className="pbadge">
          {ok > 0 ? `✓ ${ok} importé${ok > 1 ? "s" : ""}` : "Aucun import"}
        </span>
        {ko > 0 && <span className="pbadge fail">✗ {ko}</span>}
        {skipped.length > 0 && (
          <span className="pbadge skip">↷ {skipped.length}</span>
        )}
      </div>
      {topLevelError && <div className="psub">Erreur : {topLevelError}</div>}
      {staleTool && (
        <div className="psub warn">
          L'outil de téléchargement est périmé ({status.yt_dlp_version}).
          Lance <code>{status.yt_dlp_update_cmd || "yt-dlp -U"}</code> puis réessaie.
        </div>
      )}
      {(failed.length > 0 || skipped.length > 0) && (
        <div className="ifails">
          {[...failed, ...skipped].slice(0, 6).map((entry, index) => (
            <div className="ifail" key={(entry.isrc || "") + index}>
              <span className="ifail-name">{importEntryLabel(entry)}</span>
              <span className="ifail-why">{importReasonLabel(entry)}</span>
            </div>
          ))}
          {failed.length + skipped.length > 6 && (
            <div className="ifail more">
              + {failed.length + skipped.length - 6} autre
              {failed.length + skipped.length - 6 > 1 ? "s" : ""}
            </div>
          )}
        </div>
      )}
      {retryIsrcs.length > 0 && onRetry && (
        <div
          className={"iretry " + (retrying ? "disabled" : "")}
          onClick={retrying ? undefined : () => onRetry(retryIsrcs)}
        >
          {retrying ? "Relance…" : `Réessayer ${retryIsrcs.length} morceau${retryIsrcs.length > 1 ? "x" : ""}`}
        </div>
      )}
      {!topLevelError && failed.length === 0 && skipped.length === 0 && (
        <div className="psub">Cliquer sur × pour fermer.</div>
      )}
    </div>
  )
}

function PlaylistPreview({
  preview, playing, togglePlay, isStacked, toggleStack,
  playInAppleMusic, stack, onImportAll, onImportSelection, onCancel,
  importTargetMode, setImportTargetMode, playPlaylist,
}) {
  const n = preview.tracks.length
  const skipped = preview.skipped || 0
  const selN = stack.length
  // Deux dimensions indépendantes :
  //  - existsInAppleMusic : la playlist tourne déjà dans Apple Music →
  //    on affiche le ▶ tête pour la lire directement.
  //  - readOnly : rien à importer (playlist déjà en local) → masque la
  //    barre d'import du bas. Les radios ne sont ni l'un ni l'autre :
  //    elles se lisent en preview 30s (aperçu Deezer) mais s'importent
  //    comme une playlist normale.
  const existsInAppleMusic = !!preview.existsInAppleMusic
  const readOnly = !!preview.readOnly
  return (
    <div className="preview">
      <div className="phead">
        <div className="pback" onClick={onCancel} title="Retour">←</div>
        <PlaylistArtwork
          className="phcover"
          imageUrl={preview.cover_thumb}
          covers={firstMosaicCovers(preview.tracks)}
        />
        <div className="phbody">
          <div className="phname">{preview.name}</div>
          <div className="phmeta">
            {n} titre{n > 1 ? "s" : ""}
            {preview.creator ? ` · ${preview.creator}` : ""}
            {skipped > 0 ? ` · ${skipped} sans ISRC` : ""}
          </div>
        </div>
        {existsInAppleMusic && (
          <div className="pheadactions">
            <span
              className="ibtn inlib pheadbtn"
              onClick={() => playPlaylist && playPlaylist(preview.name)}
              title="Lire dans Apple Music"
            >▶</span>
          </div>
        )}
        {!readOnly && !existsInAppleMusic && (
          <div className="pheadactions">
            <span
              className={"ibtn pheadbtn " + (selN > 0 ? "added" : "primary")}
              onMouseDown={(e) => {
                // onMouseDown au lieu de onClick : les enfants Übersicht/WebKit
                // avalent parfois le click à cause d'un handler racine
                // (bindDrag installe des listeners sur le body). MouseDown
                // capture l'événement avant la propagation.
                e.preventDefault()
                e.stopPropagation()
                if (selN > 0) onImportSelection()
                else onImportAll()
              }}
              title={
                selN > 0
                  ? `Importer ${selN} sélection${selN > 1 ? "s" : ""}`
                  : `Importer toute la playlist (${n})`
              }
            >{selN > 0 ? `✓ ${selN}` : "↓"}</span>
          </div>
        )}
      </div>
      <div className="plist anim">
        {preview.tracks.map((t, i) => (
          <div className="prow" key={t.isrc || i}>
            <div
              className="cover"
              style={{backgroundImage: t.cover_url ? `url("${t.cover_url}")` : "none"}}
            />
            <div className="body">
              <div className="tname">{t.title}</div>
              <div className="tartist">{t.artist}</div>
            </div>
            <div className="actions">
              {/* Bouton ▶ affiché dès qu'un preview_url existe. Les playlists
                  locales Apple Music n'en ont pas → bouton omis. Les radios
                  (mode readOnly) en ont via Deezer → bouton présent. */}
              {t.preview_url && (
                <span
                  className={"ibtn " + (playing === t.isrc ? "playing" : "")}
                  onClick={() => togglePlay(t)}
                  title="Aperçu 30s"
                >
                  {playing === t.isrc ? "❚❚" : "▶"}
                </span>
              )}
              {t.in_library ? (
                <span
                  className="ibtn inlib"
                  onClick={() => playInAppleMusic(t)}
                  title="Lire dans Apple Music"
                >♪</span>
              ) : (
                <span
                  className={"ibtn " + (isStacked(t.isrc) ? "added" : "")}
                  onClick={() => toggleStack(t)}
                  title="Ajouter à la file"
                >
                  {isStacked(t.isrc) ? "✓" : "+"}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      {!readOnly && (
        <div className="pimport-bar">
          <div className="modepills subtle">
            <div
              className={"modepill " + (importTargetMode === "playlist" ? "active" : "")}
              onClick={() => setImportTargetMode("playlist")}
              title="Créer / mettre à jour une playlist Apple Music"
            >Comme playlist</div>
            <div
              className={"modepill " + (importTargetMode === "classic" ? "active" : "")}
              onClick={() => setImportTargetMode("classic")}
              title="Importer les morceaux sans créer de playlist"
            >Import simple</div>
          </div>
          {selN > 0 ? (
            <div className="importbtn" onClick={onImportSelection}>
              Importer {selN} sélection{selN > 1 ? "s" : ""}
            </div>
          ) : (
            <div className="importbtn" onClick={onImportAll}>
              {importTargetMode === "classic"
                ? `Importer ${n} morceau${n > 1 ? "x" : ""}`
                : `Importer toute la playlist (${n})`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MusicPanel({statusBlock, homeBlock}) {
  let initialStatus = null
  try { initialStatus = JSON.parse(statusBlock) } catch (e) { initialStatus = null }
  let home = null
  try { home = JSON.parse(homeBlock) } catch (e) { home = null }

  const [query, setQuery] = useState("")
  const [results, setResults] = useState([])
  // 'tracks' (morceaux Deezer) | 'playlists' (playlists Deezer) |
  // 'exportify' (dépôt de CSV)
  const [mode, setMode] = useState("tracks")
  // "Pour toi" : landing = grille de mix (artistes / genres / décennies /
  // moods / récents). Le fetch initial ne résout AUCUN morceau, juste des
  // covers, donc s'affiche en < 1s. Cache disque 6h côté Python.
  // Ref du mix en cours de résolution (clic sur une case) — sert à
  // afficher l'indicateur "Chargement…" sur la carte cliquée.
  // ISRC / deezer_id en cours de résolution "Radio de ce titre".
  const [playlistResults, setPlaylistResults] = useState([])
  // deezer_id de la playlist dont on est en train de résoudre les tracks (clic en cours)
  const [resolvingId, setResolvingId] = useState(0)
  // Aperçu avant import : { name, creator, nb_tracks, tracks: [...], skipped }
  // Quand non-null, on remplace la grille de résultats par la liste détaillée.
  const [previewPlaylist, setPreviewPlaylist] = useState(null)
  const [stack, setStack] = useState([])
  const [searching, setSearching] = useState(false)
  const [playing, setPlaying] = useState(null)
  const [errorMsg, setErrorMsg] = useState("")
  // Cible d'import quand on lance depuis un PlaylistPreview (Deezer ou CSV) :
  // "playlist" (créer/append playlist Apple Music) | "classic" (juste les tracks).
  const [importTargetMode, setImportTargetMode] = useState("playlist")
  const [csvProcessing, setCsvProcessing] = useState(false)
  const [csvError, setCsvError] = useState("")
  const [dragActive, setDragActive] = useState(false)
  // Nom de la playlist Apple Music dont on est en train de charger les tracks.
  const [resolvingHomePlaylist, setResolvingHomePlaylist] = useState("")
  // Bouton "voir plus" : par défaut limite à 2 lignes (6 cellules en 3-col grid).
  const [showAllPlaylists, setShowAllPlaylists] = useState(false)
  const [cancellingImport, setCancellingImport] = useState(false)
  // Statut polled localement (toutes les 1.5s pendant un import). La refresh
  // globale Übersicht ne se déclenche qu'à 60s — trop lent pour montrer
  // l'avancement.
  const [status, setStatus] = useState(initialStatus)
  const [dismissedRunAt, setDismissedRunAt] = useState("")
  const [retryingImport, setRetryingImport] = useState(false)
  const audioRef = useRef(null)
  const reqIdRef = useRef(0)
  // Identifiant de l'import qu'on attend. `import-status` renvoie le fichier
  // du run précédent tant que le worker détaché n'a pas écrit le sien : sans
  // ce garde-fou, le premier poll lisait le résultat d'avant, croyait l'import
  // terminé et arrêtait de suivre celui qui venait de démarrer.
  const runIdRef = useRef("")

  function homePlaylistUid(p) {
    return (p && (p.persistent_id || p.name)) || ""
  }

  // Sync : si le statusBlock parvient via props (refresh global), prendre
  // la valeur la plus récente.
  // Sync depuis les props : on n'écrase un état local "running" que si le
  // statut entrant est non-vide. Sinon le push de la commande détachée vers
  // running serait effacé par le tout premier render (statusBlock vide tant
  // que le worker n'a pas écrit widget_status.json).
  useEffect(() => {
    if (!initialStatus || !initialStatus.status) return
    // Pendant qu'on attend un run précis, le refresh global ne doit pas nous
    // repasser le statut d'un run antérieur.
    if (runIdRef.current && initialStatus.run_id !== runIdRef.current) return
    setStatus(initialStatus)
  }, [statusBlock])

  // Polling : pendant `running` ou juste après, rafraîchir 1.5s.
  // Garde-fou : si le worker `--detach` crashe avant d'écrire widget_status.json,
  // le polling ne recevrait jamais de status valide et la barre resterait
  // bloquée "running" indéfiniment. On reset après 45s sans aucune
  // confirmation du worker.
  useEffect(() => {
    if (!status || status.status !== "running") return
    const startedAt = Date.now()
    let sawWorker = false
    const id = setInterval(async () => {
      try {
        const out = await run(`${MM_RUN} import-status 2>/dev/null`)
        const parsed = JSON.parse((out || "").trim() || "{}")
        // Payload vide, ou statut appartenant au run précédent : le worker
        // --detach n'a pas encore écrit le sien (~1-3s de cold-start Python +
        // chargement des stores). Surtout ne pas conclure que c'est fini.
        const expected = runIdRef.current
        const stale = expected && parsed && parsed.run_id !== expected
        if (!parsed || !parsed.status || stale) {
          if (!sawWorker && Date.now() - startedAt > 45000) {
            clearInterval(id)
            setStatus({
              status: "done",
              completed: [],
              failed: [],
              finished_at: new Date().toISOString(),
              error: "le worker n'a pas démarré (aucun statut après 45s)",
            })
          }
          return
        }
        sawWorker = true
        setStatus(parsed)
        if (parsed.status !== "running") clearInterval(id)
      } catch (e) {}
    }, 1500)
    return () => clearInterval(id)
  }, [status && status.status])

  useEffect(() => {
    if (mode === "exportify") {
      // Mode Exportify : pas de recherche textuelle, on liste les CSVs locaux.
      setResults([]); setPlaylistResults([])
      setSearching(false); setErrorMsg("")
      return
    }
    const q = query.trim()
    if (!q) {
      setResults([]); setPlaylistResults([])
      setSearching(false); setErrorMsg("")
      return
    }
    setSearching(true); setErrorMsg("")
    const myId = ++reqIdRef.current
    const t = setTimeout(async () => {
      try {
        const arg = escapeShellArg(q)
        const cmd = mode === "playlists"
          ? `${MM_RUN} search-playlists ${arg} --limit 9`
          : `${MM_RUN} search ${arg} --limit 8`
        const out = await run(cmd)
        if (myId !== reqIdRef.current) return
        const trimmed = (out || "").trim()
        if (!trimmed) {
          setErrorMsg("Sortie vide.")
          setResults([]); setPlaylistResults([])
          return
        }
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
          if (mode === "playlists") { setPlaylistResults(parsed); setResults([]) }
          else { setResults(parsed); setPlaylistResults([]) }
        } else if (parsed && parsed.error) {
          setErrorMsg("Erreur : " + parsed.error)
          setResults([]); setPlaylistResults([])
        } else {
          setErrorMsg("Réponse inattendue")
          setResults([]); setPlaylistResults([])
        }
      } catch (e) {
        if (myId === reqIdRef.current) {
          setErrorMsg("Échec : " + String(e).slice(0, 80))
          setResults([]); setPlaylistResults([])
        }
      } finally {
        if (myId === reqIdRef.current) setSearching(false)
      }
    }, 400)
    return () => clearTimeout(t)
  }, [query, mode])

  function openExportifySite() {
    run(`open https://exportify.app 2>/dev/null`).catch(() => {})
  }

  function handleCsvDrop(e) {
    e.preventDefault()
    setDragActive(false)
    const files = e.dataTransfer && e.dataTransfer.files
    if (!files || files.length === 0) return
    processCsvFile(files[0])
  }

  async function openFilePicker() {
    // Übersicht/WebKit ignore `<input type="file">.click()` → on passe par
    // un dialogue natif via osascript. Renvoie le chemin absolu sélectionné
    // (vide si l'utilisateur annule).
    if (csvProcessing) return
    let filePath = ""
    try {
      const out = await run(
        `osascript -e 'POSIX path of (choose file with prompt "Choisir un CSV à importer" of type {"csv"} default location (path to downloads folder))' 2>/dev/null`
      )
      filePath = (out || "").trim()
    } catch (e) {
      return  // user a annulé
    }
    if (!filePath) return
    processCsvPath(filePath, filePath.split("/").pop())
  }

  async function processCsvPath(filePath, displayName) {
    // Chemin déjà absolu sur disque : on appelle direct le CLI.
    setCsvProcessing(true)
    setCsvError("")
    setStack([])
    setErrorMsg("")
    try {
      const out = await run(
        `${MM_RUN} exportify-process-csv ${escapeShellArg(filePath)} 2>/dev/null`
      )
      const trimmed = (out || "").trim()
      const parsed = JSON.parse(trimmed || "{}")
      if (parsed && parsed.error) {
        setCsvError("Erreur : " + parsed.error)
        return
      }
      const tracks = Array.isArray(parsed.tracks) ? parsed.tracks : []
      if (tracks.length === 0) {
        setCsvError("Aucun titre avec ISRC reconnu sur Deezer.")
        return
      }
      const skipNoIsrc = parsed.skipped_no_isrc || 0
      const skipNoDeezer = parsed.skipped_not_on_deezer || 0
      const fallbackName = (displayName || "Playlist").replace(/\.csv$/i, "")
      setPreviewPlaylist({
        name: parsed.name || fallbackName,
        creator: parsed.creator || "",
        nb_tracks: parsed.nb_tracks || tracks.length,
        tracks,
        skipped: skipNoIsrc + skipNoDeezer,
        cover_url: "",
        cover_thumb: "",
      })
    } catch (e) {
      setCsvError("Échec : " + String(e).slice(0, 80))
    } finally {
      setCsvProcessing(false)
    }
  }

  async function processCsvFile(file) {
    // Drag-drop : pas de chemin absolu accessible → on lit le contenu et on
    // écrit un fichier temporaire avant d'appeler le CLI.
    if (!file || !file.name || !file.name.toLowerCase().endsWith(".csv")) {
      setCsvError("Le fichier doit être un CSV.")
      return
    }
    setCsvProcessing(true)
    setCsvError("")
    let tmpPath = ""
    try {
      const arrayBuffer = await file.arrayBuffer()
      const bytes = new Uint8Array(arrayBuffer)
      let binary = ""
      for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode.apply(
          null, bytes.subarray(i, i + 0x8000)
        )
      }
      const b64 = btoa(binary)
      const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_")
      tmpPath = `/tmp/mm_dropped_${Date.now()}_${safeName}`
      if (!tmpPath.toLowerCase().endsWith(".csv")) tmpPath += ".csv"
      await run(
        `printf '%s' ${escapeShellArg(b64)} | base64 -d > ${escapeShellArg(tmpPath)}`
      )
      // setCsvProcessing reste true via processCsvPath
      setCsvProcessing(false)
      await processCsvPath(tmpPath, file.name)
    } catch (e) {
      setCsvError("Échec : " + String(e).slice(0, 80))
      setCsvProcessing(false)
    } finally {
      if (tmpPath) {
        run(`rm -f ${escapeShellArg(tmpPath)}`).catch(() => {})
      }
    }
  }

  async function cancelImport() {
    if (cancellingImport) return
    setCancellingImport(true)
    try {
      await run(`${MM_RUN} import-cancel 2>/dev/null`)
    } catch (e) {}
    // Le polling import-status (toutes les 1.5s) verra le status "cancelled"
    // une fois le worker arrivé à un point de check. On laisse le bouton en
    // état "désactivé" jusqu'à ce que le status soit plus en "running".
  }

  async function pickHomePlaylist(p, uid) {
    if (resolvingHomePlaylist) return
    setResolvingHomePlaylist(uid || p.persistent_id || p.name)
    setErrorMsg("")
    setStack([])
    try {
      const nameArg = escapeShellArg(p.name)
      const pidArg = p.persistent_id
        ? ` --persistent-id ${escapeShellArg(p.persistent_id)}`
        : ""
      const out = await run(
        `${MM_RUN} playlist-local-tracks ${nameArg}${pidArg} 2>/dev/null`
      )
      const trimmed = (out || "").trim()
      const parsed = JSON.parse(trimmed || "{}")
      if (parsed && parsed.error) {
        setErrorMsg("Erreur : " + parsed.error)
        return
      }
      const tracks = Array.isArray(parsed.tracks) ? parsed.tracks : []
      const coverThumb = p.cover_filename
        ? `music-manager.assets/${p.cover_filename}`
        : ""
      setPreviewPlaylist({
        name: p.name,
        creator: "",
        nb_tracks: tracks.length,
        tracks,
        skipped: 0,
        cover_url: "",
        cover_thumb: coverThumb,
        readOnly: true,
        existsInAppleMusic: true,
      })
    } catch (e) {
      setErrorMsg("Échec : " + String(e).slice(0, 80))
    } finally {
      setResolvingHomePlaylist("")
    }
  }

  function togglePlay(track) {
    const a = audioRef.current
    if (!a) return
    // Stop si on reclique sur la piste en cours OU si une preview joue
    // (défensif : couvre l'état désynchronisé où `playing` ne match plus l'audio réel)
    if (playing === track.isrc || !a.paused) {
      try { a.pause() } catch (e) {}
      setPlaying(null)
      // Si on a juste voulu stopper la piste courante, on s'arrête là.
      if (playing === track.isrc) return
    }
    if (!track.preview_url) return
    a.src = track.preview_url
    a.play().catch(() => setPlaying(null))
    setPlaying(track.isrc)
  }
  function toggleStack(track) {
    if (track.in_library) return
    setStack(prev => {
      if (prev.find(t => t.isrc === track.isrc)) return prev.filter(t => t.isrc !== track.isrc)
      if (!track.isrc) return prev
      return [...prev, track]
    })
  }
  function isStacked(isrc) { return !!stack.find(t => t.isrc === isrc) }

  // Point de passage unique pour lancer un import. Retourne true si le worker
  // a bien démarré. Toutes les variantes (stack, playlist, réessai) passent
  // par ici pour que l'échappement shell et le suivi du run_id soient
  // impossibles à oublier.
  async function launchImport(isrcs, {playlistName = "", coverUrl = "", force = false} = {}) {
    const clean = (isrcs || []).filter(Boolean)
    if (!clean.length) return false
    // Les ISRC viennent d'une API externe : ils passent par le même
    // échappement que le reste du fichier, jamais par concaténation brute.
    let cmd = `${MM_RUN} import-isrcs ${escapeShellArg(clean.join(","))} --detach`
    if (playlistName) cmd += ` --playlist-name ${escapeShellArg(playlistName)}`
    if (coverUrl) cmd += ` --playlist-cover-url ${escapeShellArg(coverUrl)}`
    if (force) cmd += " --force"

    let reply = {}
    try {
      const out = await run(`${cmd} 2>/dev/null`)
      reply = JSON.parse((out || "").trim() || "{}")
    } catch (e) {
      setErrorMsg("Échec du lancement : " + String(e).slice(0, 100))
      return false
    }
    if (reply.status === "blocked") {
      setStatus({status: "blocked", reason: reply.reason || "widget_busy"})
      return false
    }
    if (reply.status !== "started") {
      setErrorMsg("L'import n'a pas démarré.")
      return false
    }
    runIdRef.current = reply.run_id || ""
    setDismissedRunAt("")
    // Force le status à "running" pour démarrer le polling immédiatement.
    setStatus({
      status: "running",
      run_id: reply.run_id || "",
      current: 0,
      total: reply.total || clean.length,
      completed: [], failed: [], skipped: [],
      current_title: "",
      playlist_name: playlistName,
      playlist_added: 0,
    })
    return true
  }

  async function doImport() {
    if (!stack.length) return
    const isrcs = stack.map(t => t.isrc)
    if (await launchImport(isrcs)) setStack([])
  }

  // Réessai explicite : `--force` court-circuite le délai anti-acharnement,
  // sinon le bouton ne ferait visiblement rien.
  async function retryFailed(isrcs) {
    if (retryingImport) return
    setRetryingImport(true)
    try {
      await launchImport(isrcs, {force: true})
    } finally {
      setRetryingImport(false)
    }
  }

  // Clic sur une playlist Deezer : on résout ses tracks (avec titres/artistes/covers)
  // et on ouvre l'aperçu — l'utilisateur voit la liste avant de lancer l'import.
  async function pickPlaylist(pl) {
    if (resolvingId) return  // un fetch déjà en cours
    setResolvingId(pl.deezer_id)
    setErrorMsg("")
    setStack([])  // la sélection mode-tracks ne fuit pas dans la preview
    try {
      const out = await run(`${MM_RUN} playlist-tracks ${pl.deezer_id} 2>/dev/null`)
      const trimmed = (out || "").trim()
      if (!trimmed) { setErrorMsg("Sortie vide depuis playlist-tracks."); return }
      const parsed = JSON.parse(trimmed)
      if (parsed && parsed.error) { setErrorMsg("Erreur : " + parsed.error); return }
      const tracks = Array.isArray(parsed.tracks) ? parsed.tracks : []
      if (tracks.length === 0) { setErrorMsg("Aucun titre reconnu dans cette playlist."); return }
      setPreviewPlaylist({
        name: parsed.name || pl.title || "Playlist",
        creator: parsed.creator || pl.creator || "",
        nb_tracks: parsed.nb_tracks || tracks.length,
        tracks,
        skipped: parsed.skipped_no_isrc || 0,
        // Cover HD (picture_xl) pour set comme artwork Apple Music ;
        // pl.picture_url (medium) reste pour l'affichage en-tête.
        cover_url: parsed.cover_url || pl.picture_url || "",
        cover_thumb: pl.picture_url || parsed.cover_url || "",
      })
    } catch (e) {
      setErrorMsg("Échec : " + String(e).slice(0, 80))
    } finally {
      setResolvingId(0)
    }
  }

  async function launchPlaylistImport(preview) {
    // On passe TOUS les ISRCs (y compris ceux déjà dans la library) : le
    // pipeline fast-path les déjà-importés et les ajoute quand même à la
    // playlist Apple Music. Le user veut une playlist complète, pas juste
    // les nouveaux téléchargements.
    const importable = preview.tracks.filter(t => t.isrc)
    if (importable.length === 0) {
      setErrorMsg("Aucun titre avec ISRC dans cette playlist.")
      setPreviewPlaylist(null)
      return
    }
    if (!(await launchImport(importable.map(t => t.isrc), _importTarget(preview)))) return
    setPreviewPlaylist(null)
    setQuery("")
    setMode("tracks")
  }

  // Importe uniquement les titres sélectionnés via le bouton + (la stack),
  // tout en les ajoutant à la playlist Apple Music du même nom (sauf si
  // sourceMode === "classic", auquel cas on n'ajoute pas à une playlist).
  async function launchPlaylistSelection(preview) {
    if (!stack.length) return
    if (!(await launchImport(stack.map(t => t.isrc), _importTarget(preview)))) return
    setStack([])
    setPreviewPlaylist(null)
    setQuery("")
    setMode("tracks")
  }

  function _importTarget(preview) {
    // Mode "classic" : aucune playlist → les tracks sont importées sans être
    // ajoutées à une playlist Apple Music.
    if (importTargetMode === "classic") return {}
    return {playlistName: preview.name, coverUrl: preview.cover_url || ""}
  }

  function closePreview() {
    setPreviewPlaylist(null)
    setStack([])  // la sélection ne survit pas à la sortie de la preview
  }
  function playInAppleMusic(track) {
    if (!track.apple_id) return
    run(`${MM_RUN} play ${track.apple_id} 2>/dev/null`)
  }
  function playPlaylist(name) {
    if (!name) return
    run(`${MM_RUN} play-playlist ${escapeShellArg(name)} 2>/dev/null`)
  }

  const importing = status && status.status === "running"
  // Le fichier de statut persiste : sans borne de fraîcheur, un refus vieux
  // d'une semaine masquerait encore la home aujourd'hui.
  const blocked = status && status.status === "blocked" && (() => {
    if (!status.at) return true  // ancien format, on fait confiance
    const t = new Date(status.at).getTime()
    return isNaN(t) || (Date.now() - t < 2 * 60 * 1000)
  })()
  const finishedAt = (status && status.finished_at) || ""
  const finishedRecently = (() => {
    if (!finishedAt) return false
    const t = new Date(finishedAt).getTime()
    return !isNaN(t) && (Date.now() - t < 5 * 60 * 1000)
  })()
  const justDone =
    status && (status.status === "done" || status.status === "cancelled") &&
    finishedRecently && finishedAt !== dismissedRunAt &&
    ((Array.isArray(status.completed) && status.completed.length > 0) ||
     (Array.isArray(status.failed) && status.failed.length > 0) ||
     (Array.isArray(status.skipped) && status.skipped.length > 0) ||
     Boolean(status.error))
  // L'import tourne en arrière-plan : on garde la home accessible pendant
  // qu'il s'exécute. Seules les conditions "query active" et "exportify mode"
  // masquent la home. La barre de progression s'affiche en bas, sticky.
  const showHome = !query && !blocked && !justDone && home && mode !== "exportify"
  // Une fois que le status passe à autre chose que "running", on reset le flag
  // local "cancellingImport" pour permettre un futur annulation.
  useEffect(() => {
    if (status && status.status !== "running" && cancellingImport) {
      setCancellingImport(false)
    }
  }, [status && status.status])
  const hasPlaylists = showHome && Array.isArray(home.playlists) && home.playlists.length > 0
  const hasRecent = showHome && Array.isArray(home.recent) && home.recent.length > 0

  // Mode preview : remplace tout le panel par la vue détaillée de la playlist
  // sélectionnée. On la place avant le rendu principal pour ne pas avoir à
  // wrapper toutes les sections dans des conditions.
  if (previewPlaylist) {
    return (
      <div className="scroll">
        <audio ref={audioRef} onEnded={() => setPlaying(null)}/>
        <PlaylistPreview
          preview={previewPlaylist}
          playing={playing}
          togglePlay={togglePlay}
          isStacked={isStacked}
          toggleStack={toggleStack}
          playInAppleMusic={playInAppleMusic}
          stack={stack}
          onImportAll={() => launchPlaylistImport(previewPlaylist)}
          onImportSelection={() => launchPlaylistSelection(previewPlaylist)}
          onCancel={closePreview}
          importTargetMode={importTargetMode}
          setImportTargetMode={setImportTargetMode}
          playPlaylist={playPlaylist}
        />
      </div>
    )
  }

  return (
    <div className="scroll">
      <audio ref={audioRef} onEnded={() => setPlaying(null)}/>
      <div className="searchbox">
        <svg className="ico" viewBox="0 0 24 24" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="21" y2="21"/>
        </svg>
        <input
          placeholder={mode === "playlists" ? "Nom de playlist…" : "titre, artiste, album…"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && <span className="clearbtn" onClick={() => setQuery("")}>×</span>}
      </div>

      <div className="modepills">
        <div
          className={"modepill " + (mode === "tracks" ? "active" : "")}
          onClick={() => setMode("tracks")}
        >Morceaux</div>
        <div
          className={"modepill " + (mode === "playlists" ? "active" : "")}
          onClick={() => setMode("playlists")}
        >Playlists</div>
        <div
          className={"modepill " + (mode === "exportify" ? "active" : "")}
          onClick={() => setMode("exportify")}
        >Exportify</div>
      </div>

      {query && errorMsg && <div className="empty" style={{color: DANGER, fontWeight: 600}}>{errorMsg}</div>}
      {query && !errorMsg && searching && mode === "tracks" && results.length === 0 && (
        <div className="empty"><span className="loader"/>Recherche…</div>
      )}
      {query && !errorMsg && !searching && mode === "tracks" && results.length === 0 && (
        <div className="empty">Aucun résultat</div>
      )}
      {query && !errorMsg && searching && mode === "playlists" && playlistResults.length === 0 && (
        <div className="empty"><span className="loader"/>Recherche…</div>
      )}
      {query && !errorMsg && !searching && mode === "playlists" && playlistResults.length === 0 && (
        <div className="empty">Aucune playlist</div>
      )}

      {mode === "tracks" && results.length > 0 && <div className="anim">{results.map(r => (
        <div className="row" key={r.isrc || (r.deezer_id + ":" + r.title)}>
          <div className="cover" style={{backgroundImage: r.cover_url ? `url("${r.cover_url}")` : "none"}}/>
          <div className="body">
            <div className="tname">{r.title}</div>
            <div className="tartist">{r.artist}{r.album ? ` · ${r.album}` : ""}</div>
          </div>
          <div className="actions">
            <span className={"ibtn " + (playing === r.isrc ? "playing" : "")} onClick={() => togglePlay(r)} title="Aperçu 30s">
              {playing === r.isrc ? "❚❚" : "▶"}
            </span>
            {r.in_library ? (
              <span className="ibtn inlib" onClick={() => playInAppleMusic(r)} title="Lire dans Apple Music">♪</span>
            ) : (
              <span className={"ibtn " + (isStacked(r.isrc) ? "added" : "")} onClick={() => toggleStack(r)} title="Ajouter à la file">
                {isStacked(r.isrc) ? "✓" : "+"}
              </span>
            )}
          </div>
        </div>
      ))}</div>}

      {mode === "playlists" && playlistResults.length > 0 && (
        <div className="plgrid-search anim">
          {playlistResults.map(pl => (
            <div
              className={"plcell " + (resolvingId === pl.deezer_id ? "loading" : "")}
              key={pl.deezer_id}
              onClick={() => pickPlaylist(pl)}
              title={`Importer « ${pl.title} »${pl.creator ? " — " + pl.creator : ""}`}
            >
              <div
                className="plcover"
                style={{backgroundImage: pl.picture_url ? `url("${pl.picture_url}")` : "none"}}
              >
                {!pl.picture_url && "♫"}
                <div className="plbadge">{pl.nb_tracks} ♪</div>
                <div className="ploverlay">
                  {resolvingId === pl.deezer_id ? "Récupération…" : "Importer →"}
                </div>
              </div>
              <div className="plcaption">{pl.title}</div>
              {pl.creator && <div className="plsub">{pl.creator}</div>}
            </div>
          ))}
        </div>
      )}

      {mode === "exportify" && (
        <div className="anim">
          <div className="expobanner">
            <div className="expobtn" onClick={openExportifySite}>Ouvrir Exportify</div>
            <div className="expohint">
              Exporte ta playlist Spotify depuis exportify.app, puis dépose le CSV ci-dessous.
            </div>
          </div>

          <div
            className={
              "dropzone " +
              (dragActive ? "active " : "") +
              (csvProcessing ? "disabled" : "")
            }
            onClick={openFilePicker}
            onDragOver={(e) => { e.preventDefault(); if (!csvProcessing) setDragActive(true) }}
            onDragEnter={(e) => { e.preventDefault(); if (!csvProcessing) setDragActive(true) }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleCsvDrop}
          >
            <div className="dropicon">⤓</div>
            {csvProcessing ? (
              <><span className="loader"/>Récupération sur Deezer…</>
            ) : (
              <>
                Choisir un fichier CSV
                <div className="dropsub">format Exportify ou colonnes title/artist/isrc</div>
              </>
            )}
          </div>

          {csvError && (
            <div className="empty" style={{color: DANGER, fontWeight: 600}}>{csvError}</div>
          )}
        </div>
      )}

      {mode === "tracks" && query && stack.length > 0 && !importing && (
        <div className="importbtn" onClick={doImport}>Importer {stack.length} morceau{stack.length > 1 ? "x" : ""}</div>
      )}

      {blocked && (
        <div className="blocked-music">
          {status.reason === "ui_running" ? (
            <>Music Manager est ouvert.<br/>Ferme l'app pour importer depuis le widget.</>
          ) : (
            <>Un import est déjà en cours.<br/>Attends qu'il se termine.</>
          )}
        </div>
      )}

      {showHome && (
        <div>
          {hasPlaylists && <div className="sectitle">Playlists</div>}
          {hasPlaylists && (() => {
            const all = home.playlists
            const favs = all.filter(p => p.is_favorite)
            const others = all.filter(p => !p.is_favorite)
            // Par défaut : seulement les favoris. Bouton "Voir tout" pour
            // étendre à toutes les playlists (favoris + non-favoris).
            const visible = showAllPlaylists ? all : favs
            const hasOthers = others.length > 0
            return (
              <>
                {visible.length > 0 && (
                  <div className="plgrid anim">
                    {visible.map((p, i) => {
                      const uid = homePlaylistUid(p) || `${p.name}#${i}`
                      const coverUrl = p.cover_filename
                        ? `music-manager.assets/${p.cover_filename}`
                        : ""
                      const mosaic = playlistMosaicUrls(p)
                      return (
                        <div
                          className={"plcell " + (resolvingHomePlaylist === uid ? "loading" : "")}
                          key={uid}
                          onClick={() => pickHomePlaylist(p, uid)}
                          title={`Ouvrir « ${p.name} »`}
                        >
                          <PlaylistArtwork
                            className="plcover"
                            imageUrl={coverUrl}
                            covers={mosaic}
                          />
                          <div className="plcaption">{p.name}</div>
                        </div>
                      )
                    })}
                  </div>
                )}
                {visible.length === 0 && !showAllPlaylists && hasOthers && (
                  <div className="empty">Aucune playlist favorite pour l'instant.</div>
                )}
                {hasOthers && (
                  <div
                    className="seemore"
                    onClick={() => setShowAllPlaylists(v => !v)}
                  >
                    {showAllPlaylists
                      ? `Réduire ↑ (${favs.length} favori${favs.length > 1 ? "s" : ""})`
                      : `Voir tout ↓ (${others.length} de plus)`}
                  </div>
                )}
              </>
            )
          })()}
          {hasRecent && (
            <>
              <div className="sectitle">Récents</div>
              <div className="anim">
                {home.recent.map(t => (
                  <div className="row" key={t.apple_id} onClick={() => playInAppleMusic(t)}>
                    <div className="cover" style={{backgroundImage: t.cover_url ? `url("${t.cover_url}")` : "none"}}/>
                    <div className="body">
                      <div className="tname">{t.title}</div>
                      <div className="tartist">{t.artist}{t.album ? ` · ${t.album}` : ""}</div>
                    </div>
                    <span className="ibtn inlib" onClick={(e) => { e.stopPropagation(); playInAppleMusic(t) }} title="Lire dans Apple Music">♪</span>
                  </div>
                ))}
              </div>
            </>
          )}
          {!hasPlaylists && !hasRecent && <div className="empty">Aucune donnée musique</div>}
        </div>
      )}

      {/* Barre sticky en bas — import en cours OU fin récente. Permet à
          l'utilisateur de continuer à utiliser le widget pendant l'import. */}
      {(importing || justDone) && (
        <div className="import-sticky">
          {importing && (
            <ImportProgress
              status={status}
              onCancel={cancelImport}
              cancelling={cancellingImport}
            />
          )}
          {!importing && justDone && (
            <ImportDone
              status={status}
              onDismiss={() => setDismissedRunAt(finishedAt)}
              onRetry={retryFailed}
              retrying={retryingImport}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── Entry point ────────────────────────────────────────────────────────────
export const render = ({output}) => {
  const raw = output || ""
  const statusBlock =
    (raw.split("===MUSIC_STATUS===")[1] || "").split("===MUSIC_HOME===")[0].trim() || "{}"
  const homeBlock = (raw.split("===MUSIC_HOME===")[1] || "{}").trim() || "{}"

  bindDrag()

  return (
    <div>
      <div className="topbar dashboard-drag">
        <div className="tabsmain">
          <span className="tabmain active">
            <span className="tabico">\u266a</span>Musique
          </span>
        </div>
      </div>
      <MusicPanel statusBlock={statusBlock} homeBlock={homeBlock}/>
    </div>
  )
}

