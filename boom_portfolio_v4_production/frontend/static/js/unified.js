(()=>{'use strict';
const $$=(s,r=document)=>[...r.querySelectorAll(s)];
function purgeBooking(){
  $$('a[href*="/booking"],a[href*="admin-booking"],.contact-channel,.contact-link').forEach(el=>{
    const text=(el.textContent||'').trim().toLowerCase();
    const href=(el.getAttribute?.('href')||'').toLowerCase();
    if(href.includes('/booking')||href.includes('admin-booking')||/^booking\b/.test(text)||text.includes('book a shoot'))el.remove();
  });
}
function markActiveNav(){
  const path=location.pathname.replace(/\.html$/,'')||'/';
  $$('.nav-links a').forEach(a=>{
    const href=new URL(a.href,location.origin).pathname.replace(/\.html$/,'')||'/';
    const active=href==='/'?path==='/':path===href||path.startsWith(href+'/')||(href==='/photography'&&path.startsWith('/photography/'))||(href==='/work'&&path.startsWith('/projects/'));
    if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
  });
}
function closeMobileNavOnNavigate(){
  document.addEventListener('click',e=>{
    const link=e.target.closest('.nav-links a');
    if(!link)return;
    document.querySelector('.nav-links')?.classList.remove('open');
    const btn=document.querySelector('.menu-btn');
    if(btn)btn.setAttribute('aria-expanded','false');
  });
}
function init(){
  purgeBooking();markActiveNav();closeMobileNavOnNavigate();
  const observer=new MutationObserver(()=>purgeBooking());
  observer.observe(document.body,{childList:true,subtree:true});
  setTimeout(()=>observer.disconnect(),8000);
  document.documentElement.dataset.ui='ready';
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();