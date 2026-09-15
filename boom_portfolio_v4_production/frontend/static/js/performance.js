(()=>{'use strict';
const CACHE_KEY='csboom_perf_cache_v1';
const memory={projects:null,albums:null};
const transparent='data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
function readCache(){try{return JSON.parse(sessionStorage.getItem(CACHE_KEY)||'{}')}catch{return{}}}
function writeCache(patch){try{sessionStorage.setItem(CACHE_KEY,JSON.stringify({...readCache(),...patch}))}catch{}}
async function fetchJson(url){const r=await fetch(url,{headers:{Accept:'application/json'}});if(!r.ok)throw new Error(`Request failed (${r.status})`);return r.json()}
function getProjects(){if(memory.projects)return memory.projects;memory.projects=(async()=>{const cached=readCache().projects;if(cached?.rows?.length&&Date.now()-cached.saved<300000)return cached.rows;const rows=await fetchJson('/api/projects');writeCache({projects:{saved:Date.now(),rows}});return rows})().catch(e=>{memory.projects=null;throw e});return memory.projects}
function getAlbums(){if(memory.albums)return memory.albums;memory.albums=(async()=>{let cached;try{cached=JSON.parse(localStorage.getItem('csboom_album_cache')||'null')}catch{}if(cached?.rows?.length&&Date.now()-cached.saved<120000)return cached.rows;const rows=await fetchJson('/api/albums');try{localStorage.setItem('csboom_album_cache',JSON.stringify({saved:Date.now(),rows}))}catch{}return rows})().catch(e=>{memory.albums=null;throw e});return memory.albums}

// One browser-selected image request per tile. The old LQIP flow requested a tiny
// Drive image first and then requested the real image, which doubled gallery traffic.
if(typeof window.imageVariant==='function'){
  window.responsiveImage=function(url,{alt='',width=null,height=null,sizes='(max-width:700px) 50vw, 25vw',eager=false,className=''}={}){
    const src=window.imageVariant(url,eager?1400:900,82);
    const srcset=[480,800,1200,1600].map(w=>`${window.imageVariant(url,w,82)} ${w}w`).join(',');
    const dims=width&&height?` width="${window.esc(width)}" height="${window.esc(height)}"`:'';
    return `<img class="lqip-image is-developed ${window.esc(className)}" src="${window.esc(src||transparent)}" srcset="${window.esc(srcset)}" sizes="${window.esc(sizes)}" loading="${eager?'eager':'lazy'}" decoding="async" fetchpriority="${eager?'high':'auto'}" alt="${window.esc(alt)}"${dims}>`;
  };
  window.initResponsiveImages=function(){};
}

// Home used to request projects twice and albums twice. These overrides share one
// request per resource and reuse it for cards, filters and stats.
if(typeof window.loadProjects==='function'){
  window.loadProjects=async function(target='#projectGrid',featured=null){
    const el=document.querySelector(target);if(!el)return;
    el.innerHTML=window.skeletonCards(target==='#featuredProjects'?3:6);
    try{
      let rows=await getProjects();
      if(featured===true){const picked=rows.filter(p=>p.is_featured);rows=(picked.length?picked:rows).slice(0,3)}
      if(target==='#featuredProjects')rows=rows.slice(0,3);
      const render=list=>{el.innerHTML=list.length?list.map(window.projectCard).join(''):'<div class="empty">No projects published yet.</div>'};
      render(rows);
      if(target==='#projectGrid')window.setupFilters('#projectFilters',rows,{attr:window.projectKind,onFilter:value=>render(value?rows.filter(p=>window.projectKind(p)===value):rows)});
    }catch{el.innerHTML=window.friendlyError("Couldn't load the projects.")}
  };
}
if(typeof window.loadAlbums==='function'){
  window.loadAlbums=async function(){
    const el=document.querySelector('#albumGrid');if(!el)return;
    el.innerHTML=window.skeletonCards(3);
    try{
      let rows=await getAlbums();
      if(document.body.classList.contains('home-page'))rows=rows.slice(0,3);
      const render=list=>{el.innerHTML=list.length?list.map(window.albumCard).join(''):'<div class="empty">No albums are available right now. Please try again.</div>'};
      render(rows);
      window.setupFilters('#albumFilters',rows,{attr:window.albumCategory,onFilter:value=>render(value?rows.filter(a=>window.albumCategory(a)===value):rows)});
    }catch{el.innerHTML=window.friendlyError("Couldn't load the albums.")}
  };
}
if(typeof window.loadHomeStats==='function'){
  window.loadHomeStats=async function(){
    const projectEl=document.querySelector('#homeProjectCount'),photoEl=document.querySelector('#homePhotoCount'),ideaEl=document.querySelector('#homeIdeaCount');
    if(!projectEl&&!photoEl&&!ideaEl)return;
    try{
      const [projects,albums]=await Promise.all([getProjects(),getAlbums()]);
      const photoCount=(albums||[]).reduce((sum,a)=>sum+Number(a.photo_count||0),0);
      if(projectEl)projectEl.textContent=String((projects||[]).length);
      if(photoEl)photoEl.textContent=String(photoCount);
      if(ideaEl)ideaEl.textContent=String((projects||[]).length+photoCount+(albums||[]).length);
    }catch{if(projectEl)projectEl.textContent='0';if(photoEl)photoEl.textContent='0';if(ideaEl)ideaEl.textContent='0'}
  };
}

// Neighbour prefetch should use an optimized derivative, not the full Drive original.
if(typeof window.prefetchNeighbor==='function'&&typeof window.imageVariant==='function'){
  window.prefetchNeighbor=function(){
    if(!window.lightboxItems?.length)return;
    [-1,1].forEach(step=>{const p=window.lightboxItems[(window.lightboxIndex+step+window.lightboxItems.length)%window.lightboxItems.length];if(p){const img=new Image();img.decoding='async';img.src=window.imageVariant(p.image_url,1200,78)}});
  };
}
})();