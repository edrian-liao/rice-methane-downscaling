import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, pandas as pd, geopandas as gpd, numpy as np, rasterio
import cartopy.crs as ccrs, cartopy.feature as cfeature, cartopy.io.shapereader as shpreader
from shapely.ops import unary_union
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import warnings; warnings.filterwarnings("ignore")

plt.rcParams.update({'font.size':14,'axes.titlesize':17,'axes.labelsize':15,
    'xtick.labelsize':13,'ytick.labelsize':13,'legend.fontsize':13,
    'legend.title_fontsize':14,'figure.titlesize':20})

gauges=pd.read_csv("data/2026_WaterLevel_Runkle/gauges_2026.csv")
idx=pd.read_csv("nisar_granule_index.csv"); cov=pd.read_csv("nisar_coverage_long.csv")
k=idx[idx.granule.isin(cov.granule.unique())]
fr=gpd.GeoDataFrame(k,geometry=gpd.GeoSeries.from_wkt(k.footprint_wkt),crs=4326)

shp=shpreader.natural_earth(resolution="10m",category="cultural",name="admin_1_states_provinces")
recs=[r for r in shpreader.Reader(shp).records() if r.attributes["admin"]=="United States of America"]
ark=[r.geometry for r in recs if r.attributes["name"]=="Arkansas"]
states=[r.geometry for r in recs]

# rice mask -> points for a light background stipple
with rasterio.open("data/downscaling_files/rice_mask_imi_2024.tif") as s:
    a=s.read(1); tr=s.transform; crs=s.crs
r,c=np.nonzero(a==1); sel=slice(None,None,40)
xs,ys=rasterio.transform.xy(tr,r[sel],c[sel])
rice=gpd.GeoSeries(gpd.points_from_xy(xs,ys),crs=crs).to_crs(4326)

COL={12:"#e8702a",84:"#1f77b4",105:"#2ca02c"}
LS={"DESC":"-","ASC":"--"}
pc=ccrs.PlateCarree()

fig=plt.figure(figsize=(25,10.5))
ax=fig.add_subplot(1,3,1,projection=pc)
ax.set_extent([-95.2,-88.6,32.6,36.9],crs=pc)
ax.add_feature(cfeature.LAND,facecolor="#f4f1ea"); ax.add_feature(cfeature.OCEAN,facecolor="#d6e6f0")
ax.add_feature(cfeature.RIVERS,edgecolor="#8fb8d4",linewidth=0.7)
ax.add_geometries(states,crs=pc,facecolor="none",edgecolor="#b0b0b0",linewidth=0.9)
ax.add_geometries(ark,crs=pc,facecolor="none",edgecolor="#333333",linewidth=2.4,zorder=6)
ax.scatter(rice.x,rice.y,s=1.2,c="#7fc97f",alpha=0.55,transform=pc,zorder=3,label="rice (2024 mask)")

LABEL_POS={12:(-94.05,35.72),84:(-89.85,35.55),105:(-93.55,33.15)}
for (t,f,d),sub in fr.groupby(["track","frame","direction"]):
    u=unary_union(list(sub.geometry))
    ax.add_geometries([u],crs=pc,facecolor=COL[t],alpha=0.13,edgecolor="none",zorder=4)
    ax.add_geometries([u],crs=pc,facecolor="none",edgecolor=COL[t],linewidth=2.6,
                      linestyle=LS[d],zorder=7)
    lx,ly=LABEL_POS[t]
    ax.text(lx,ly,f"T{t} F{f} {d}\n{len(sub)} scenes",color=COL[t],
            fontsize=13,fontweight="bold",ha="center",va="center",transform=pc,zorder=9,
            bbox=dict(fc="white",ec=COL[t],alpha=0.92,boxstyle="round,pad=0.3"))

ax.scatter(gauges.lon,gauges.lat,s=120,c="#d62728",edgecolor="white",linewidth=1.6,
           zorder=11,transform=pc)
gl=ax.gridlines(draw_labels=True,linewidth=0.5,color="gray",alpha=0.35,linestyle=":")
gl.top_labels=gl.right_labels=False
ax.set_title("NISAR frames covering the 2026 Runkle gauges")
ax.legend(handles=[
    Line2D([0],[0],marker="o",color="none",markerfacecolor="#d62728",markeredgecolor="w",
           markersize=12,label="gauge (7 fields)"),
    Patch(facecolor="#7fc97f",alpha=.7,label="rice (2024 mask)"),
    Line2D([0],[0],color=COL[12],lw=2.6,ls="-",label="track 12 / frame 71  DESC"),
    Line2D([0],[0],color=COL[84],lw=2.6,ls="-",label="track 84 / frame 71  DESC"),
    Line2D([0],[0],color=COL[105],lw=2.6,ls="--",label="track 105 / frame 19  ASC"),
    Line2D([0],[0],color="#333",lw=2.4,label="Arkansas"),
],loc="lower right",framealpha=0.95)

# ---- zoom on the gauge cluster -------------------------------------------
ax2=fig.add_subplot(1,3,2,projection=pc)
pad=0.09
ax2.set_extent([gauges.lon.min()-pad,gauges.lon.max()+pad,
                gauges.lat.min()-pad,gauges.lat.max()+pad],crs=pc)
try:
    import cartopy.io.img_tiles as cimgt
    class Esri(cimgt.GoogleTiles):
        def _image_url(self,t):
            x,y,z=t; return ("https://server.arcgisonline.com/ArcGIS/rest/services/"
                             f"World_Imagery/MapServer/tile/{z}/{y}/{x}")
    ax2.add_image(Esri(),11)
except Exception as e:
    print("tiles unavailable:",e); ax2.add_feature(cfeature.LAND,facecolor="#f4f1ea")
ax2.scatter(rice.x,rice.y,s=9,c="#7fc97f",alpha=0.8,transform=pc,zorder=3)
OFF={"S16":(42,18),"R8":(-70,18),"S0":(42,-10),
     "W3-EC":(58,34),"W3-CS":(58,8),"W4-EC":(58,-18),"W4-CS":(58,-44)}
for _,g in gauges.iterrows():
    ax2.scatter(g.lon,g.lat,s=170,c="#d62728",edgecolor="white",linewidth=2,zorder=11,transform=pc)
    ax2.annotate(g.gauge_id,(g.lon,g.lat),xytext=OFF.get(g.gauge_id,(10,8)),
                 textcoords="offset points",fontsize=12,fontweight="bold",color="white",
                 zorder=12,arrowprops=dict(arrowstyle="-",color="white",lw=1.2),
                 bbox=dict(fc="#d62728",ec="white",alpha=0.92,boxstyle="round,pad=0.2"))
gl2=ax2.gridlines(draw_labels=True,linewidth=0.5,color="white",alpha=0.4,linestyle=":")
gl2.top_labels=gl2.right_labels=False
ax2.set_title("Gauge cluster — all 7 in the same frames")
span_km=111*max(gauges.lat.max()-gauges.lat.min(),
                (gauges.lon.max()-gauges.lon.min())*np.cos(np.radians(34.6)))
ax2.text(0.02,0.02,f"extent ≈ {span_km:.0f} km",transform=ax2.transAxes,fontsize=12,
         color="white",bbox=dict(fc="black",alpha=0.55,boxstyle="round,pad=0.3"))
way=gauges[gauges.gauge_id.str.startswith("W")]
ax3=fig.add_subplot(1,3,3,projection=pc)
p3=0.006
ax3.set_extent([way.lon.min()-p3,way.lon.max()+p3,way.lat.min()-p3,way.lat.max()+p3],crs=pc)
try:
    ax3.add_image(Esri(),15)
except Exception as e:
    print("tiles unavailable (panel 3):",e)
for _,g in way.iterrows():
    ax3.scatter(g.lon,g.lat,s=240,c="#d62728",edgecolor="white",linewidth=2.4,zorder=11,transform=pc)
    ax3.annotate(f"{g.gauge_id}  ({g.field_name}, {g.acres:.0f} ac)",(g.lon,g.lat),
                 xytext=(14,10),textcoords="offset points",fontsize=13,fontweight="bold",
                 color="white",zorder=12,
                 arrowprops=dict(arrowstyle="-",color="white",lw=1.4),
                 bbox=dict(fc="#d62728",ec="white",alpha=0.93,boxstyle="round,pad=0.25"))
gl3=ax3.gridlines(draw_labels=True,linewidth=0.5,color="white",alpha=0.4,linestyle=":")
gl3.top_labels=gl3.right_labels=False
ax3.set_title("Way 3 / Way 4 block")
dkm=111*(way.lat.max()-way.lat.min())
ax3.text(0.02,0.02,f"these 4 span ≈ {dkm:.1f} km",transform=ax3.transAxes,fontsize=12,
         color="white",bbox=dict(fc="black",alpha=0.6,boxstyle="round,pad=0.3"))

fig.suptitle("2026 Runkle gauges and the NISAR passes that cover them",y=0.97)
plt.tight_layout(rect=[0,0,1,0.95])
plt.savefig("gauge_frame_map.png",dpi=120,bbox_inches="tight")
print("saved gauge_frame_map.png")
