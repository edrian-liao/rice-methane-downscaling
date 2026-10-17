#!/usr/bin/env python3
"""Calendar of NISAR passes, field visits, and the matches between them."""
import matplotlib; matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D

plt.rcParams.update({'font.size':14,'axes.titlesize':17,'axes.labelsize':15,
    'xtick.labelsize':13,'ytick.labelsize':14,'legend.fontsize':13,
    'legend.title_fontsize':14,'figure.titlesize':20})

TRACK_COLOR = {12:"#e8702a", 84:"#1f77b4", 105:"#2ca02c"}

t = pd.read_csv("nisar_coverage_long.csv")
t["acq"] = pd.to_datetime(t.acq_datetime_utc).dt.tz_localize(None).dt.normalize()
r = pd.read_csv("data/2026_WaterLevel_Runkle/readings_2026.csv")
r["date"] = pd.to_datetime(r.date)

gauges = sorted(t.gauge_id.unique())
y = {g: i for i, g in enumerate(gauges)}

fig, ax = plt.subplots(figsize=(19, 7))
for g in gauges:
    d = t[t.gauge_id == g]
    for track, sub in d.groupby("track"):
        ax.scatter(sub.acq, [y[g]] * len(sub), marker="|", s=420, linewidths=2.4,
                   color=TRACK_COLOR.get(track, "#888"), zorder=3)
    v = r[r.gauge_id == g]
    ax.scatter(v.date, [y[g] - 0.22] * len(v), marker="o", s=52,
               facecolor="none", edgecolor="#555555", linewidths=1.6, zorder=4)
    m = d[d.gauge_reading.notna()]
    ax.scatter(m.acq, [y[g]] * len(m), marker="*", s=420, color="#d62728",
               edgecolor="white", linewidths=0.9, zorder=6)

ax.set_yticks(range(len(gauges)))
ax.set_yticklabels([f"{g}  ({t[(t.gauge_id==g)].gauge_reading.notna().sum()} matched)"
                    for g in gauges])
ax.set_ylim(-0.7, len(gauges) - 0.4)
ax.invert_yaxis()
ax.xaxis.set_major_locator(mdates.MonthLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %-d"))
ax.xaxis.set_minor_locator(mdates.WeekdayLocator(byweekday=0))
ax.grid(axis="x", which="major", alpha=0.35)
ax.grid(axis="x", which="minor", alpha=0.12)
for i in range(len(gauges)):
    ax.axhline(i, color="#dddddd", lw=0.8, zorder=1)
ax.set_xlabel("2026")
ax.set_title("NISAR passes vs field visits — same-day matches starred")
ax.legend(handles=[
    Line2D([0],[0],marker="|",color=TRACK_COLOR[12],lw=0,markersize=16,markeredgewidth=2.4,label="pass, track 12 (DESC)"),
    Line2D([0],[0],marker="|",color=TRACK_COLOR[84],lw=0,markersize=16,markeredgewidth=2.4,label="pass, track 84 (DESC)"),
    Line2D([0],[0],marker="|",color=TRACK_COLOR[105],lw=0,markersize=16,markeredgewidth=2.4,label="pass, track 105 (ASC)"),
    Line2D([0],[0],marker="o",color="none",markerfacecolor="none",markeredgecolor="#555",markersize=9,label="field visit"),
    Line2D([0],[0],marker="*",color="none",markerfacecolor="#d62728",markeredgecolor="w",markersize=18,label="matched (±0 d)"),
], loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=5, frameon=False)
plt.tight_layout()
fig.savefig("coverage_calendar.png", dpi=120, bbox_inches="tight")
print("saved coverage_calendar.png")
