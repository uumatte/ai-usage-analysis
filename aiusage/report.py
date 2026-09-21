"""Run the analysis SQL and draw the report figures.

    python -m aiusage.report

Full results go to Data/analysis/ (private); public copies without personal
topics go to reports/tables/, and figures to reports/figures/ (light + dark).
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # draw to files, no window

import matplotlib.dates
import matplotlib.pyplot as plt
import matplotlib.ticker
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path

from aiusage.build_db import connect
from aiusage.common import ROOT

ANALYSIS_SQL = ROOT / "sql" / "analysis"
PRIVATE_OUT = ROOT / "Data" / "analysis"
TABLES_OUT = ROOT / "reports" / "tables"
FIGURES_OUT = ROOT / "reports" / "figures"

# Topics about looks, health and relationships stay out of the public report.
# Edit this set before publishing; the private CSVs always keep everything.
PERSONAL_TAGS = {
    "makeup", "skin-care", "cosmetic-treatments", "hair-styling", "eye-care",
    "appearance", "clothing-fashion", "shaving-grooming", "fitness-exercise",
    "health-symptoms", "human-body", "mental-health", "sleep", "diet-nutrition",
    "dating", "social-relationships", "self-improvement",
}
TAG_COLUMNS = ("topic", "tag_a", "tag_b")

# --- Visual system ---

PLATFORMS = ["chatgpt", "claude_web", "gemini", "claude_code", "codex"]  # fixed slot order
PLATFORM_LABELS = {"chatgpt": "ChatGPT", "claude_web": "Claude (web)", "gemini": "Gemini",
                   "claude_code": "Claude Code", "codex": "Codex"}
VENDORS = ["chatgpt", "claude", "gemini"]  # same hues as their web platforms
VENDOR_LABELS = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini"}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

THEMES = {
    "light": {
        "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
        # sequential blue, near-zero recedes toward the light surface
        "ramp": ["#f3f7fd", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
    },
    "dark": {
        "surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
        # near-zero recedes toward the dark surface, high values get brighter
        "ramp": ["#1f2530", "#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
    },
}

DPI = 100            # layout is designed at 100 px per inch ...
SAVE_DPI = 200       # ... and saved at 2x for sharp screens
PX = 72 / DPI        # one design pixel in points
RADIUS_PX = 4
BAR_MAX_PX = 24

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 9,
    "axes.titlesize": 9,
    "svg.fonttype": "none",
})


def platform_color(theme, platform):
    return theme["series"][PLATFORMS.index(platform)]


def vendor_color(theme, vendor):
    return theme["series"][VENDORS.index(vendor)]


def text_on(fill_hex, theme):
    """White or ink text, whichever reads better on a filled mark."""
    r, g, b = to_rgb(fill_hex)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0b0b0b" if luminance > 0.55 else "#ffffff"


def new_figure(theme, width_px, height_px, margins):
    """Figure with fixed margins (left, right, top, bottom in px) so pixel math is exact."""
    fig = plt.figure(figsize=(width_px / DPI, height_px / DPI), dpi=DPI, facecolor=theme["surface"])
    left, right, top, bottom = margins
    ax = fig.add_axes([left / width_px, bottom / height_px,
                       1 - (left + right) / width_px, 1 - (top + bottom) / height_px])
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["axis"])
        ax.spines[side].set_linewidth(1 * PX)
    ax.tick_params(colors=theme["muted"], labelcolor=theme["ink2"], length=0, pad=6)
    return fig, ax


def add_title(fig, theme, title, subtitle, height_px):
    fig.text(16 / fig.get_figwidth() / DPI, 1 - 14 / height_px, title, color=theme["ink"],
             fontsize=12, family=["Segoe UI Semibold", "DejaVu Sans"], va="top")
    fig.text(16 / fig.get_figwidth() / DPI, 1 - 36 / height_px, subtitle, color=theme["ink2"],
             fontsize=9, va="top")


def hairline_grid(ax, theme, axis="y"):
    ax.grid(True, axis=axis, color=theme["grid"], linewidth=1 * PX, linestyle="-")
    ax.set_axisbelow(True)


def px_per_unit(ax):
    (x0, y0), (x1, y1) = ax.transData.transform([(0, 0), (1, 1)])
    return abs(x1 - x0), abs(y1 - y0)


def bar_thickness(ax, spacing=1.0, axis="x"):
    """Bar width in data units: at most 24px, and leave air between bars."""
    ppx, ppy = px_per_unit(ax)
    per_unit = ppx if axis == "x" else ppy
    return min(0.62 * spacing, BAR_MAX_PX / per_unit)


def rounded_bar(ax, x, y, w, h, color, horizontal=False, round_end=True):
    """Bar with a 4px rounded data end and a square baseline, drawn as one path (no seams)."""
    if w <= 0 or h <= 0:
        return
    if not round_end:
        ax.add_patch(Rectangle((x, y), w, h, facecolor=color, edgecolor="none"))
        return
    ppx, ppy = px_per_unit(ax)
    rx, ry = min(RADIUS_PX / ppx, w / 2), min(RADIUS_PX / ppy, h / 2)
    L, C, M, Z = Path.LINETO, Path.CURVE3, Path.MOVETO, Path.CLOSEPOLY
    # CURVE3 takes two vertices: the corner as control point, then the end point.
    if horizontal:  # baseline on the left, rounded corners on the right
        vertices = [(x, y), (x + w - rx, y), (x + w, y), (x + w, y + ry), (x + w, y + h - ry),
                    (x + w, y + h), (x + w - rx, y + h), (x, y + h), (x, y)]
        codes = [M, L, C, C, L, C, C, L, Z]
    else:           # baseline at the bottom, rounded corners on top
        vertices = [(x, y), (x + w, y), (x + w, y + h - ry), (x + w, y + h), (x + w - rx, y + h),
                    (x + rx, y + h), (x, y + h), (x, y + h - ry), (x, y)]
        codes = [M, L, L, C, C, L, C, C, Z]
    ax.add_patch(PathPatch(Path(vertices, codes), facecolor=color, edgecolor="none"))


def legend(ax, theme, labels_colors, loc="upper left", anchor=(0, 1.02), ncol=5, kind="square"):
    marker = "s" if kind == "square" else None
    handles = [Line2D([0], [0], marker=marker, linestyle="none" if marker else "-", markersize=8,
                      markerfacecolor=color, markeredgecolor=color, color=color,
                      linewidth=2 * PX * 1.4) for _, color in labels_colors]
    leg = ax.legend(handles, [label for label, _ in labels_colors], loc=loc, bbox_to_anchor=anchor,
                    ncol=ncol, frameon=False, handletextpad=0.4, columnspacing=1.2, borderaxespad=0)
    for text in leg.get_texts():
        text.set_color(theme["ink2"])


def save(fig, name, mode):
    FIGURES_OUT.mkdir(parents=True, exist_ok=True)
    suffix = "" if mode == "light" else "-dark"
    fig.savefig(FIGURES_OUT / f"{name}{suffix}.png", dpi=SAVE_DPI, facecolor=fig.get_facecolor())
    plt.close(fig)


# --- Figures (each takes the public table(s) and a theme) ---

def fig_weekly_prompts(t, theme, mode):
    df = t["03_weekly_prompts"].copy()
    df["week"] = pd.to_datetime(df["week"])
    wide = df.pivot_table(index="week", columns="platform", values="prompts", aggfunc="sum").fillna(0)
    wide = wide.reindex(columns=[p for p in PLATFORMS if p in wide.columns])
    W, H = 880, 420
    fig, ax = new_figure(theme, W, H, (56, 20, 96, 44))
    x = range(len(wide))
    ax.set_xlim(-0.6, len(wide) - 0.4)
    ax.set_ylim(0, wide.sum(axis=1).max() * 1.08)
    hairline_grid(ax, theme)
    width = bar_thickness(ax)
    for i, (_, row) in enumerate(wide.iterrows()):
        bottom = 0
        nonzero = [p for p in wide.columns if row[p] > 0]
        for p in nonzero:
            rounded_bar(ax, i - width / 2, bottom, width, row[p], platform_color(theme, p),
                        round_end=(p == nonzero[-1]))
            bottom += row[p]
            if p != nonzero[-1]:  # 2px surface gap between stacked segments
                ax.plot([i - width / 2, i + width / 2], [bottom, bottom], color=theme["surface"],
                        linewidth=2 * PX, solid_capstyle="butt", zorder=3)
    ax.set_xticks(list(x), [d.strftime("%b %d") for d in wide.index], rotation=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    for label in ax.get_xticklabels():
        label.set_fontsize(8)
    legend(ax, theme, [(PLATFORM_LABELS[p], platform_color(theme, p)) for p in wide.columns],
           anchor=(0, 1.10))
    add_title(fig, theme, "Prompts per week, by platform",
              "Week starting (Hong Kong time), Jun–Sep 2026 · one 2023 conversation left out", H)
    save(fig, "weekly_prompts", mode)


def fig_vendor_share(t, theme, mode):
    df = t["04_monthly_ai_share"]
    df = df[df["session_ai"].isin(VENDORS)].copy()
    df["month"] = pd.to_datetime(df["month"])
    wide = df.pivot_table(index="month", columns="session_ai", values="prompts", aggfunc="sum").fillna(0)
    wide = wide.reindex(columns=[v for v in VENDORS if v in wide.columns])
    share = wide.div(wide.sum(axis=1), axis=0) * 100
    W, H = 880, 300
    fig, ax = new_figure(theme, W, H, (56, 24, 96, 40))
    ax.set_xlim(0, 100)
    ax.set_ylim(len(share) - 0.4, -0.6)
    ax.spines["left"].set_visible(False)
    thickness = bar_thickness(ax, axis="y")
    for i, (month, row) in enumerate(share.iterrows()):
        left = 0
        vendors = [v for v in share.columns if row[v] > 0]
        for v in vendors:
            color = vendor_color(theme, v)
            rounded_bar(ax, left, i - thickness / 2, row[v], thickness, color, horizontal=True,
                        round_end=(v == vendors[-1]))
            if row[v] >= 9:  # label only segments wide enough to hold the text
                ax.text(left + row[v] / 2, i, f"{row[v]:.0f}%", ha="center", va="center",
                        fontsize=8, color=text_on(color, theme), zorder=4)
            left += row[v]
            if v != vendors[-1]:
                ax.plot([left, left], [i - thickness / 2, i + thickness / 2], color=theme["surface"],
                        linewidth=2 * PX, solid_capstyle="butt", zorder=3)
    ax.set_yticks(range(len(share)), [m.strftime("%b") for m in share.index])
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    legend(ax, theme, [(VENDOR_LABELS[v], vendor_color(theme, v)) for v in share.columns],
           anchor=(0, 1.22))
    add_title(fig, theme, "Which AI got the prompts, month by month",
              "Share of typed prompts per vendor (web and CLI combined; 1 DeepSeek prompt left out)", H)
    save(fig, "vendor_share", mode)


def fig_hour_weekday(t, theme, mode):
    df = t["05_hour_weekday"]
    grid = df.pivot_table(index="weekday", columns="hour", values="prompts", aggfunc="sum")
    grid = grid.reindex(index=range(1, 8), columns=range(24)).fillna(0)
    W, H = 880, 330
    fig, ax = new_figure(theme, W, H, (56, 96, 80, 40))
    cmap = LinearSegmentedColormap.from_list("seq", theme["ramp"])
    mesh = ax.pcolormesh(grid.values, cmap=cmap, edgecolors=theme["surface"], linewidth=2 * PX)
    ax.set_ylim(7, 0)
    ax.set_yticks([i + 0.5 for i in range(7)], WEEKDAYS)
    ax.set_xticks([h + 0.5 for h in range(0, 24, 3)], [f"{h:02d}:00" for h in range(0, 24, 3)])
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    cax = fig.add_axes([1 - 70 / W, 40 / H, 12 / W, 1 - 120 / H])
    cbar = fig.colorbar(mesh, cax=cax)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=theme["muted"], labelcolor=theme["ink2"], length=0, labelsize=8)
    cbar.set_label("prompts", color=theme["ink2"], fontsize=8)
    add_title(fig, theme, "When prompts are typed",
              "Prompts per weekday and hour · all times converted to Hong Kong time (UTC+8)", H)
    save(fig, "hour_weekday", mode)


def fig_activity_by_platform(t, theme, mode):
    df = t["10_activity_by_platform"]
    order = (t["11_short_sessions_by_activity"].sort_values("sessions", ascending=False)["activity"].tolist())
    grid = df.pivot_table(index="platform", columns="activity", values="pct_of_platform", aggfunc="sum")
    grid = grid.reindex(index=PLATFORMS, columns=order).fillna(0)
    W, H = 880, 330
    fig, ax = new_figure(theme, W, H, (100, 96, 80, 40))
    cmap = LinearSegmentedColormap.from_list("seq", theme["ramp"])
    mesh = ax.pcolormesh(grid.values, cmap=cmap, vmin=0, vmax=80,
                         edgecolors=theme["surface"], linewidth=2 * PX)
    ax.set_ylim(len(grid), 0)
    ax.set_yticks([i + 0.5 for i in range(len(grid))], [PLATFORM_LABELS[p] for p in grid.index])
    ax.set_xticks([i + 0.5 for i in range(len(order))], order)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    for i, platform in enumerate(grid.index):
        for j, activity in enumerate(order):
            value = grid.loc[platform, activity]
            if value >= 15:  # label only the cells that tell the story
                color = cmap(value / 80)
                ax.text(j + 0.5, i + 0.5, f"{value:.0f}%", ha="center", va="center", fontsize=8,
                        color=text_on(matplotlib.colors.to_hex(color), theme))
    cax = fig.add_axes([1 - 70 / W, 40 / H, 12 / W, 1 - 120 / H])
    cbar = fig.colorbar(mesh, cax=cax)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=theme["muted"], labelcolor=theme["ink2"], length=0, labelsize=8)
    cbar.set_label("% of the platform's sessions", color=theme["ink2"], fontsize=8)
    add_title(fig, theme, "What each platform is used for",
              "Share of sessions by activity tag (one per session); cells from 15% are labelled", H)
    save(fig, "activity_by_platform", mode)


def horizontal_value_bars(theme, mode, labels, values, name, title, subtitle, fmt, width_px=880):
    H = 90 + 30 * len(labels)
    fig, ax = new_figure(theme, width_px, H, (130, 70, 76, 30))
    ax.set_xlim(0, max(values) * 1.12)
    ax.set_ylim(len(labels) - 0.4, -0.6)
    ax.spines["left"].set_visible(False)
    hairline_grid(ax, theme, axis="x")
    thickness = bar_thickness(ax, axis="y")
    color = theme["series"][0]  # one series -> one colour
    for i, value in enumerate(values):
        rounded_bar(ax, 0, i - thickness / 2, value, thickness, color, horizontal=True)
        ax.text(value + max(values) * 0.012, i, fmt(value), va="center", fontsize=8, color=theme["ink2"])
    ax.set_yticks(range(len(labels)), labels)
    ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    add_title(fig, theme, title, subtitle, H)
    save(fig, name, mode)


def fig_short_sessions(t, theme, mode):
    df = t["06_short_sessions"].sort_values("short_pct", ascending=False)
    horizontal_value_bars(theme, mode, [PLATFORM_LABELS[p] for p in df["platform"]],
                          df["short_pct"].tolist(), "short_sessions",
                          "How often a session ends after one or two prompts",
                          "Share of sessions with at most 2 typed prompts", lambda v: f"{v:.1f}%")


def fig_top_topics(t, theme, mode):
    df = t["12_topics"]
    df = df[df["topic"] != "unclear-topic"].head(15)  # fallback tag, not a topic
    horizontal_value_bars(theme, mode, df["topic"].tolist(), df["sessions"].tolist(), "top_topics",
                          "Most frequent topics", "Sessions per topic tag (a session can have several; "
                          "personal topics hidden)", lambda v: f"{v:,.0f}")


def fig_topic_effort(t, theme, mode):
    df = t["17_topic_effort"]
    W, H = 880, 520
    fig, ax = new_figure(theme, W, H, (60, 30, 76, 50))
    ax.set_xlim(0, df["sessions"].max() * 1.08)
    ax.set_ylim(0, df["avg_prompts"].max() * 1.12)
    # no grid here: the two median lines are the only reference lines
    ax.axvline(df["sessions"].median(), color=theme["axis"], linewidth=1 * PX)
    ax.axhline(df["avg_prompts"].median(), color=theme["axis"], linewidth=1 * PX)
    ax.text(df["sessions"].median(), ax.get_ylim()[1], " median sessions", va="top", fontsize=8,
            color=theme["muted"])
    ax.text(ax.get_xlim()[1], df["avg_prompts"].median(), "median prompts ", va="bottom", ha="right",
            fontsize=8, color=theme["muted"])
    ax.scatter(df["sessions"], df["avg_prompts"], s=(9 * PX) ** 2, color=theme["series"][0],
               edgecolors=theme["surface"], linewidths=2 * PX, zorder=3)
    labelled = pd.concat([df.nlargest(5, "sessions"), df.nlargest(5, "avg_prompts")]).drop_duplicates("topic")
    for row in labelled.itertuples():
        near_right = row.sessions > ax.get_xlim()[1] * 0.8  # keep labels inside the plot
        ax.annotate(row.topic, (row.sessions, row.avg_prompts), xytext=(-6 if near_right else 6, 4),
                    textcoords="offset points", ha="right" if near_right else "left",
                    fontsize=8, color=theme["ink2"])
    ax.set_xlabel("sessions", color=theme["ink2"])
    ax.set_ylabel("average prompts per session", color=theme["ink2"])
    add_title(fig, theme, "Effort by topic: how often vs. how deep",
              "Topics with at least 10 sessions; lines mark the medians (personal topics hidden)", H)
    save(fig, "topic_effort", mode)


def fig_prompt_position(t, theme, mode):
    df = t["08_prompt_length_by_position"]
    W, H = 880, 380
    fig, ax = new_figure(theme, W, H, (56, 70, 96, 44))
    hairline_grid(ax, theme)
    series = [("web", "Web chats", theme["series"][0]), ("cli", "Coding CLIs", theme["series"][1])]
    ax.set_xlim(0.7, 10.3)
    ax.set_ylim(0, df["median_chars"].max() * 1.15)
    for source, label, color in series:
        part = df[df["source"] == source].sort_values("prompt_no")
        ax.plot(part["prompt_no"], part["median_chars"], color=color, linewidth=2 * PX * 1.4,
                solid_capstyle="round", solid_joinstyle="round", zorder=3)
        ax.scatter(part["prompt_no"], part["median_chars"], s=(8 * PX) ** 2, color=color,
                   edgecolors=theme["surface"], linewidths=2 * PX, zorder=4)
        last = part.iloc[-1]
        ax.text(last["prompt_no"] + 0.18, last["median_chars"], label, va="center", fontsize=8,
                color=theme["ink2"])
    ax.set_xticks(range(1, 11))
    ax.set_xlabel("prompt number within the session", color=theme["ink2"])
    legend(ax, theme, [(label, color) for _, label, color in series], anchor=(0, 1.12), kind="line")
    add_title(fig, theme, "The first prompt carries the context",
              "Median characters of the Nth prompt in a session", H)
    save(fig, "prompt_position", mode)


def fig_new_topics(t, theme, mode):
    df = t["15_new_topics_over_time"].copy()
    df["week"] = pd.to_datetime(df["week"])
    # repeat the last value one week later so the final step is drawn
    df = pd.concat([df, df.tail(1).assign(week=df["week"].iloc[-1] + pd.Timedelta(days=7))],
                   ignore_index=True)
    W, H = 880, 340
    fig, ax = new_figure(theme, W, H, (56, 90, 80, 44))
    hairline_grid(ax, theme)
    color = theme["series"][0]
    ax.plot(df["week"], df["cumulative_topics"], color=color, linewidth=2 * PX * 1.4,
            solid_capstyle="round", solid_joinstyle="round", drawstyle="steps-post")
    ax.fill_between(df["week"], df["cumulative_topics"], step="post", color=color, alpha=0.10, linewidth=0)
    last = df.iloc[-1]
    ax.scatter([last["week"]], [last["cumulative_topics"]], s=(8 * PX) ** 2, color=color,
               edgecolors=theme["surface"], linewidths=2 * PX, zorder=4)
    ax.text(last["week"], last["cumulative_topics"], f"  {last['cumulative_topics']:.0f} topics",
            va="center", fontsize=8, color=theme["ink2"])
    ax.set_ylim(0, df["cumulative_topics"].max() * 1.15)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    add_title(fig, theme, "New topics dried up after mid-July",
              "Distinct topic tags seen so far, by week of first appearance · tags come from a ~100-tag library", H)
    save(fig, "new_topics", mode)


FIGURES = [fig_weekly_prompts, fig_vendor_share, fig_hour_weekday, fig_activity_by_platform,
           fig_short_sessions, fig_top_topics, fig_topic_effort, fig_prompt_position, fig_new_topics]


# --- Running ---

def hide_personal(df: pd.DataFrame) -> pd.DataFrame:
    mask = pd.Series(False, index=df.index)
    for col in TAG_COLUMNS:
        if col in df.columns:
            mask |= df[col].isin(PERSONAL_TAGS)
    return df[~mask]


def run_queries() -> dict[str, pd.DataFrame]:
    PRIVATE_OUT.mkdir(parents=True, exist_ok=True)
    TABLES_OUT.mkdir(parents=True, exist_ok=True)
    con = connect(read_only=True)
    tables = {}
    for sql_file in sorted(ANALYSIS_SQL.glob("[0-9][0-9]_*.sql")):
        df = con.execute(sql_file.read_text(encoding="utf-8")).df()
        df.to_csv(PRIVATE_OUT / f"{sql_file.stem}.csv", index=False, encoding="utf-8-sig")
        public = hide_personal(df)
        public.to_csv(TABLES_OUT / f"{sql_file.stem}.csv", index=False, encoding="utf-8-sig")
        tables[sql_file.stem] = public
        print(f"{sql_file.stem}: {len(df)} rows ({len(df) - len(public)} personal rows hidden)")
    con.close()
    return tables


def main() -> None:
    tables = run_queries()
    for mode, theme in THEMES.items():
        for draw in FIGURES:
            draw(tables, theme, mode)
    print(f"figures written to {FIGURES_OUT}")


if __name__ == "__main__":
    main()
