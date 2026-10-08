/* Compatible with huashu-art-motion render.py and qa.py. */
(() => {
  const canvas=document.getElementById('c'),ctx=canvas.getContext('2d');
  const params=new URLSearchParams(location.search);
  const theme=params.get('theme') || 'A';
  const capture=params.get('render')==='1';
  window.__canvas=canvas;window.__total=48;
  const previous=document.createElement('canvas');previous.width=1920;previous.height=1080;
  const next=document.createElement('canvas');next.width=1920;next.height=1080;
  function draw(c,era,t){c.save();c.clearRect(0,0,1920,1080);KEYNOTE_SCENES[era.id](c,t,theme);c.restore();}
  window.prepare=async()=>{};
  window.renderSolo=(id,lt)=>{const e=ERAS.find(e=>e.id===id);if(!e)throw Error(`Unknown scene ${id}`);draw(ctx,e,lt);};
  window.renderFrame=t=>{
    t=Math.max(0,Math.min(48-1/30,t));
    const i=Math.min(5,Math.floor(t/8)),e=ERAS[i],lt=t-e.t0;
    if(i && lt<e.transition.dur){
      draw(previous.getContext('2d'),ERAS[i-1],8+lt);draw(next.getContext('2d'),e,lt);
      const p=KEYNOTE.smooth(lt/e.transition.dur),x=-240+p*2400;
      ctx.clearRect(0,0,1920,1080);ctx.drawImage(previous,0,0);
      ctx.save();ctx.beginPath();ctx.rect(0,0,Math.max(0,x),1080);ctx.clip();ctx.drawImage(next,0,0);ctx.restore();
      const g=ctx.createLinearGradient(x-65,0,x+65,0);g.addColorStop(0,KEYNOTE.themes[theme].teal+'00');g.addColorStop(.48,KEYNOTE.themes[theme].teal+'33');g.addColorStop(.5,'#d2fff280');g.addColorStop(.52,KEYNOTE.themes[theme].teal+'33');g.addColorStop(1,KEYNOTE.themes[theme].teal+'00');ctx.fillStyle=g;ctx.fillRect(x-65,0,130,1080);
    }else draw(ctx,e,lt);
    return i;
  };
  const play=document.getElementById('play'),scrub=document.getElementById('scrub'),time=document.getElementById('tt'),sound=document.getElementById('sound'),audio=document.getElementById('audio');
  let playing=false,base=0,epoch=0,soundOn=false;
  function show(t){renderFrame(t);scrub.value=t;time.textContent=`${t.toFixed(1)} / 48.0`;
    const current=Math.min(5,Math.floor(t/8));document.querySelectorAll('[data-scene]').forEach((el,i)=>el.classList.toggle('active',i===current));}
  function pause(){base=Number(scrub.value);playing=false;audio.pause();play.textContent='播放';}
  function start(){if(base>=47.96)base=0;epoch=performance.now();playing=true;play.textContent='暂停';if(soundOn){audio.currentTime=base;audio.play().catch(()=>{});}requestAnimationFrame(loop);}
  function loop(now){if(!playing)return;const t=base+(now-epoch)/1000;if(t>=48){show(48-1/30);pause();return;}show(t);requestAnimationFrame(loop);}
  play.onclick=()=>playing?pause():start();scrub.oninput=()=>{pause();base=+scrub.value;show(base);audio.currentTime=base;};
  sound.onclick=()=>{soundOn=!soundOn;sound.textContent=soundOn?'音乐：开':'音乐：关';audio.muted=!soundOn;if(soundOn&&playing){audio.currentTime=+scrub.value;audio.play().catch(()=>{});}};
  document.querySelectorAll('[data-scene]').forEach(el=>el.onclick=()=>{pause();base=Number(el.dataset.scene)*8;show(base);audio.currentTime=base;});
  document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT')return;if(e.code==='Space'){e.preventDefault();playing?pause():start();}if(e.code==='Home'){pause();base=0;show(0);}if(['ArrowLeft','ArrowRight'].includes(e.code)){e.preventDefault();pause();base=Math.max(0,Math.min(47.9667,base+(e.code==='ArrowLeft'?-1:1)/30));show(base);}});
  if(capture)document.body.classList.add('render');
  KEYNOTE.load().then(()=>{window.__ready=true;show(capture?0:3.5);base=capture?0:3.5;}).catch(e=>{window.__bootFailed=String(e);console.error(e);});
})();
