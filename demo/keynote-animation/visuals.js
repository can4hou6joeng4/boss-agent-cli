/* All motion is a function of time. No frame-to-frame state or live platform data. */
window.KEYNOTE = (() => {
  const W = 1920, H = 1080;
  const themes = {
    A: {name:'极光发布会', bg:'#060a10', ink:'#f3f6f7', dim:'#99a6af', teal:'#00d7b1', orange:'#ff6244', card:'#0d1822', line:'#ffffff20', light:false},
    B: {name:'白昼产品剧场', bg:'#f1f3ef', ink:'#14232b', dim:'#687c80', teal:'#008f80', orange:'#ea6444', card:'#ffffff', line:'#163e3424', light:true},
    C: {name:'终端信号', bg:'#0a0b0b', ink:'#f9f5ec', dim:'#a1a19a', teal:'#27efbb', orange:'#ff6b39', card:'#151918', line:'#f0ece32b', light:false},
  };
  let logo;
  const clamp = v => Math.max(0,Math.min(1,v));
  const ease = v => 1-Math.pow(1-clamp(v),4);
  const smooth = v => {v=clamp(v);return v*v*v*(v*(v*6-15)+10);};
  const mix = (a,b,p) => a+(b-a)*p;
  const fade = (t,start=0,dur=.9) => ease((t-start)/dur);
  const rr = (c,x,y,w,h,r=24) => {c.beginPath();c.roundRect(x,y,w,h,r);};
  function text(c,s,x,y,size=32,color='#fff',weight=500,align='left',mono=false) {
    c.save();c.fillStyle=color;c.font=`${weight} ${size}px ${mono?'"SFMono-Regular", "PT Mono", monospace':'Inter, "Keynote CJK", sans-serif'}`;
    c.textAlign=align;c.textBaseline='alphabetic';c.fillText(s,x,y);c.restore();
  }
  function enter(c,t,start,fn,dur=.9,dy=32) {
    const p=fade(t,start,dur);if(!p)return;
    c.save();c.globalAlpha*=p;c.translate(0,dy*(1-p));fn(p);c.restore();
  }
  function glow(c,x,y,r,color,alpha) {
    c.save();c.globalAlpha=alpha;const g=c.createRadialGradient(x,y,0,x,y,r);g.addColorStop(0,color);g.addColorStop(1,color+'00');c.fillStyle=g;c.fillRect(x-r,y-r,r*2,r*2);c.restore();
  }
  function background(c,t,key='A') {
    const a=themes[key];c.fillStyle=a.bg;c.fillRect(0,0,W,H);
    glow(c,1320+150*Math.sin(t/6.9),490+80*Math.cos(t/8.2),690,a.teal,a.light?.1:.13);
    glow(c,1560+160*Math.cos(t/7.7),760+95*Math.sin(t/9.3),550,a.orange,a.light?.09:.12);
    glow(c,620+140*Math.sin(t/10.1),1120,690,'#3469ba',a.light?.04:.12);
    if(key==='C') {
      c.save();c.strokeStyle='#ffffff0c';c.lineWidth=1;
      for(let x=0;x<W;x+=80){c.beginPath();c.moveTo(x,0);c.lineTo(x,H);c.stroke();}
      for(let y=0;y<H;y+=80){c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();}c.restore();
    }
    c.save();c.strokeStyle=a.line;c.lineWidth=1;c.beginPath();c.moveTo(104,142);c.lineTo(1816,142);c.moveTo(104,958);c.lineTo(1816,958);c.stroke();c.restore();
    text(c,'boss-agent-cli',104,99,24,a.ink,600);
    text(c,'PRODUCT KEYNOTE  /  3.0.0',1816,98,18,a.dim,500,'right',true);
  }
  function panel(c,x,y,w,h,a,{r=28,alpha=1}={}) {
    c.save();c.globalAlpha*=alpha;c.shadowColor=a.light?'#15352715':'#00000070';c.shadowBlur=45;c.shadowOffsetY=24;
    rr(c,x,y,w,h,r);c.fillStyle=a.card;c.fill();c.shadowBlur=0;c.shadowOffsetY=0;
    const g=c.createLinearGradient(x,y,x+w,y+h);g.addColorStop(0,a.light?'#ffffff':'#ffffff30');g.addColorStop(.5,a.line);g.addColorStop(1,a.line);
    c.strokeStyle=g;c.lineWidth=1.5;c.stroke();c.restore();
  }
  function pill(c,s,x,y,w,a,color=a.teal) {
    rr(c,x,y,w,46,23);c.fillStyle=color+(a.light?'16':'18');c.fill();c.strokeStyle=color+'55';c.lineWidth=1;c.stroke();text(c,s,x+w/2,y+31,21,color,500,'center');
  }
  function brand(c,x,y,size=180) {if(logo)c.drawImage(logo,x-size/2,y-size/2,size,size);}
  function orbits(c,t,x,y,a,key='A',scale=1) {
    c.save();c.translate(x,y);c.scale(scale,scale);
    for(let i=0;i<3;i++) {
      c.save();c.rotate(.17+i*.29);c.scale(1,.65+i*.07);c.beginPath();
      if(key==='C')c.roundRect(-260-i*43,-260-i*43,520+i*86,520+i*86,52);else c.arc(0,0,260+i*43,0,Math.PI*2);
      c.strokeStyle=(i===1?a.orange:a.teal)+'26';c.lineWidth=1.2;c.stroke();c.restore();
      const ang=t*(.26+i*.11)+i*2.1,r=260+i*43;
      const px=Math.cos(ang)*r,py=Math.sin(ang)*r*(.66+i*.07);
      glow(c,px,py,23,i===1?a.orange:a.teal,.48);c.beginPath();c.arc(px,py,3.3,0,Math.PI*2);c.fillStyle=i===1?a.orange:a.teal;c.fill();
    }c.restore();
  }
  function terminalBar(c,x,y,w,a,label='boss / local session') {
    panel(c,x,y,w,166,a,{r:24});
    for(let i=0;i<3;i++){c.beginPath();c.arc(x+30+i*18,y+25,4,0,Math.PI*2);c.fillStyle=['#ff695b','#eac35d','#30b98c'][i];c.fill();}
    text(c,label,x+w-26,y+31,16,a.dim,500,'right',true);
    c.strokeStyle=a.line;c.beginPath();c.moveTo(x+1,y+49);c.lineTo(x+w-1,y+49);c.stroke();
    text(c,'›',x+30,y+114,34,a.teal,600);text(c,'boss',x+68,y+114,36,a.ink,600,'left',true);
  }
  function hero(c,t,key='A') {
    const a=themes[key];background(c,t,key);
    enter(c,t,.1,()=>text(c,'为真人与 AI Agent 而造',112,282,25,a.teal,500));
    enter(c,t,.35,()=>text(c,'一句命令，',104,456,106,a.ink,800));
    enter(c,t,.65,()=>text(c,'连接机会。',104,589,106,key==='C'?a.orange:a.ink,800));
    enter(c,t,1.05,()=>text(c,'把发现、筛选与沟通，接成一个工作流。',112,671,29,a.dim,500));
    enter(c,t,1.35,()=>{pill(c,'求职者',112,728,136,a);pill(c,'招聘者',264,728,136,a,a.orange);});
    const s=mix(.72,1,fade(t,.5,1.5));c.save();c.globalAlpha=fade(t,.45,1.2);orbits(c,t,1430,460,a,key,s);c.restore();
    enter(c,t,.6,()=>{c.save();c.translate(1430,447+Math.sin(t*.79)*7);const q=1+.012*Math.sin(t*.61);c.scale(q,q);brand(c,0,0,270);c.restore();},1.3,42);
    enter(c,t,1.65,()=>{const y=720+Math.sin(t*.92)*5;terminalBar(c,1194,y,472,a);c.save();c.globalAlpha=.55+.4*Math.pow(Math.sin(t*2.7),2);c.fillStyle=a.teal;c.fillRect(1380,y+87,3,34);c.restore();},1,80);
    text(c,'BOSS 直聘  ·  纯终端向导  ·  Schema 驱动',112,903,21,a.dim,500);
    text(c,'01 / 06',1816,903,18,a.dim,500,'right',true);
  }
  async function load() {
    await Promise.all([
      ...[['Inter','assets/Inter-var.woff',{weight:'100 900'}],['Keynote CJK','assets/NotoSansSC-500.woff',{weight:'500'}],['Keynote CJK','assets/NotoSansSC-800.woff',{weight:'800'}]].map(async([family,url,desc])=>{const f=new FontFace(family,`url(${url})`,desc);await f.load();document.fonts.add(f);}),
      new Promise((res,rej)=>{logo=new Image();logo.onload=res;logo.onerror=rej;logo.src='assets/logo.png';}),
    ]);await document.fonts.ready;
  }
  return {W,H,themes,clamp,ease,smooth,mix,fade,rr,text,enter,glow,background,panel,pill,brand,orbits,terminalBar,hero,load};
})();
