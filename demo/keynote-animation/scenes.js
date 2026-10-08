/* Six release-film scenes. Layout remains inside the 104/108px title-safe frame. */
window.KEYNOTE_SCENES = (() => {
  const K=KEYNOTE,{text,enter,background,panel,pill,brand,orbits,rr,glow,clamp,ease,smooth,mix,fade}=K;
  const footer=(c,n,a,label)=>{text(c,label,112,903,21,a.dim,500);text(c,`0${n} / 06`,1816,903,18,a.dim,500,'right',true);};
  function title(c,t,a,kicker,headline,sub) {
    enter(c,t,.35,()=>text(c,kicker,112,238,21,a.teal,500),.7,22);
    enter(c,t,.6,()=>text(c,headline,104,336,76,a.ink,800),.85,32);
    enter(c,t,.9,()=>text(c,sub,112,392,27,a.dim,500),.75,24);
  }
  function check(c,x,y,a,p=1,negative=false) {
    c.save();c.globalAlpha*=p;c.strokeStyle=negative?a.orange:a.teal;c.lineWidth=3;c.lineCap='round';c.lineJoin='round';c.beginPath();
    if(negative){c.moveTo(x-6,y-6);c.lineTo(x+6,y+6);c.moveTo(x+6,y-6);c.lineTo(x-6,y+6);}
    else{c.moveTo(x-7,y);c.lineTo(x-1,y+6);c.lineTo(x+9,y-8);}c.stroke();c.restore();
  }
  function packet(c,t,start,x0,y0,x1,y1,color,period=3,phase=0) {
    if(t<start)return;const p=((t-start)/period+phase)%1;
    const alpha=Math.pow(Math.sin(p*Math.PI),.7);const x=mix(x0,x1,p),y=mix(y0,y1,p);
    c.save();c.globalAlpha*=alpha;glow(c,x,y,15,color,.5);c.beginPath();c.arc(x,y,4.1,0,Math.PI*2);c.fillStyle=color;c.fill();c.restore();
  }
  function rail(c,t,start,x,y,w,color) {
    const p=fade(t,start,1);c.save();c.globalAlpha*=p;c.strokeStyle=color+'45';c.lineWidth=2;c.beginPath();c.moveTo(x,y);c.lineTo(x+w*p,y);c.stroke();packet(c,t,start,x,y,x+w,y,color,2.7);packet(c,t,start,x,y,x+w,y,color,2.7,.5);c.restore();
  }
  function search(c,t,key='A') {
    const a=K.themes[key];background(c,8+t,key);
    title(c,t,a,'01 / 发现与福利筛选','你在意的，才是条件。','搜索职位，逐条检查详情，同时满足你的福利关键词。');
    enter(c,t,1.15,()=>{
      panel(c,112,435,1696,131,a,{r:24});
      text(c,'SEARCH / ZHIPIN',145,470,16,a.dim,500,'left',true);
      const cmd='boss search "Python" --city 上海 --welfare "双休,五险一金"';
      const str=cmd.slice(0,Math.round(cmd.length*fade(t,1.35,1.3)));
      text(c,'›',146,526,32,a.teal,600);text(c,str,184,525,29,a.ink,500,'left',true);
      if(t<3.15 || t>6.5){c.save();c.globalAlpha=.3+.45*Math.pow(Math.sin(t*3.1),2);c.font='500 29px "SFMono-Regular", "PT Mono", monospace';c.fillStyle=a.teal;c.fillRect(188+c.measureText(str).width,502,3,28);c.restore();}
      const scan=clamp((t-1.3)/1.7);c.fillStyle=a.teal;c.fillRect(136,559,1648*scan,2);
    },.8,45);
    const jobs=[['示例 A','Python 后端工程师','20–30K','双休','五险一金',true],['示例 B','AI 应用工程师','25–35K','双休','五险一金',true],['示例 C','数据开发工程师','18–28K','单休','五险一金',false]];
    jobs.forEach((j,i)=>{
      const st=2.15+i*.2,y=603+Math.sin(t*.83+i*.8)*3+(i===2?24*fade(t,5.35,.7):0),x=112+i*576;
      enter(c,t,st,()=>{
        const dim=i===2?mix(1,.3,fade(t,5.35,.7)):1;c.save();const inheritedAlpha=c.globalAlpha;c.globalAlpha*=dim;panel(c,x,y,544,252,a);
        text(c,j[0],x+30,y+43,17,a.dim,500);text(c,j[2],x+514,y+45,26,a.teal,600,'right');
        text(c,j[1],x+30,y+102,32,a.ink,800);
        pill(c,j[3],x+30,y+126,108,a,j[5]?a.teal:a.orange);pill(c,j[4],x+152,y+126,151,a);
        c.strokeStyle=a.line;c.beginPath();c.moveTo(x+30,y+193);c.lineTo(x+514,y+193);c.stroke();
        const done=fade(t,3.6+i*.38,.7);
        c.globalAlpha=inheritedAlpha;
        check(c,x+40,y+224,a,done,!j[5]);
        text(c,done>.95?(j[5]?'两项匹配 · 保留':'未满足双休 · 排除'):'正在检查详情…',x+62,y+233,21,done>.95?(j[5]?a.teal:a.orange):a.dim,500);
        c.globalAlpha=inheritedAlpha*dim;
        const pulse=(Math.sin((t-st)*1.8)+1)/2;
        c.strokeStyle=(j[5]?a.teal:a.orange)+Math.round(14+16*pulse).toString(16).padStart(2,'0');c.lineWidth=2;rr(c,x,y,544,252,28);c.stroke();
        c.restore();
      },.8,60);
    });
    footer(c,2,a,'示意职位与薪资 · 福利条件采用 AND 匹配');
  }
  function roles(c,t,key='A') {
    const a=K.themes[key];background(c,16+t,key);
    title(c,t,a,'02 / 双角色工作流','两种角色，一个工作流。','从寻找岗位，到筛选候选人。共用同一套能力与状态。');
    const info=[{x:112,color:a.teal,title:'求职者',en:'FOR JOB SEEKERS',steps:['发现职位','本地整理','投递沟通'],note:'搜索 · 候选池 · 简历 / AI · 沟通'},{x:1032,color:a.orange,title:'招聘者',en:'FOR RECRUITERS',steps:['发现人才','处理简历','推进沟通'],note:'候选人 · 附件简历 · 聊天 · 职位管理'}];
    info.forEach((v,i)=>{
      const y=456+Math.sin(t*.71+i)*4;
      enter(c,t,1.15+i*.22,()=>{
        panel(c,v.x,y,776,380,a);text(c,v.en,v.x+38,y+47,16,v.color,500,'left',true);
        text(c,v.title,v.x+38,y+119,52,a.ink,800);
        for(let k=0;k<3;k++){
          const x=v.x+38+k*241,st=1.85+k*.4+i*.13;
          enter(c,t,st,()=>{rr(c,x,y+183,218,76,18);c.fillStyle=v.color+'0f';c.fill();c.strokeStyle=v.color+'44';c.lineWidth=1;c.stroke();text(c,v.steps[k],x+109,y+232,27,a.ink,500,'center');},.6,22);
        }
        rail(c,t,2.1,v.x+63,y+291,650,v.color);text(c,v.note,v.x+38,y+344,24,a.dim,500);
      },.9,65);
    });
    enter(c,t,1.65,()=>{orbits(c,t,960,637,a,key,.28);brand(c,960,637+Math.sin(t*.8)*3,112);},.9,15);
    footer(c,3,a,'BOSS 直聘 · 求职者 / 招聘者 · 流程画面为示意');
  }
  function agent(c,t,key='A') {
    const a=K.themes[key];background(c,24+t,key);
    title(c,t,a,'03 / 一个能力核心，四种入口','让 Agent，接得住。','先发现能力，再接入工作流。结构化输出，便于继续执行。');
    enter(c,t,1.25,()=>pill(c,'boss schema',112,449,249,a),.75,22);
    enter(c,t,1.55,()=>{
      const num=Math.round(77*(1-Math.pow(1-clamp((t-1.55)/1.35),5)));
      text(c,String(num),104,718,240,a.ink,800);text(c,'个已实现 MCP 工具',116,783,35,a.teal,500);
      text(c,'39 个顶层命令  /  13 个招聘者子命令',116,841,23,a.dim,500);
    },.9,42);
    enter(c,t,1.1,()=>{
      panel(c,900,439,908,414,a);
      const core={x:1354,y:650};
      const nodes=[{x:950,y:477,s:'CLI',small:'终端向导',color:a.teal},{x:1540,y:477,s:'JSON',small:'统一信封',color:a.teal},{x:950,y:698,s:'MCP',small:'Agent 工具',color:a.orange},{x:1540,y:698,s:'Python',small:'类型化 API',color:a.orange}];
      nodes.forEach((v,i)=>{
        const x=v.x+109,y=v.y+64;rail(c,t,1.6+i*.2,x,y,core.x-x,v.color);
        c.save();c.strokeStyle=v.color+'38';c.lineWidth=1.5;c.beginPath();c.moveTo(x,y);c.lineTo(core.x,core.y);c.stroke();packet(c,t,1.65,x,y,core.x,core.y,v.color,2.8,i*.18);c.restore();
        enter(c,t,1.35+i*.18,()=>{
          rr(c,v.x,v.y,218,124,24);c.fillStyle=a.card;c.fill();c.strokeStyle=v.color+'50';c.stroke();
          text(c,v.s,x,v.y+49,32,a.ink,600,'center',true);text(c,v.small,x,v.y+91,22,a.dim,500,'center');
        },.7,24);
      });
      glow(c,core.x,core.y,120,a.teal,.16);orbits(c,t,core.x,core.y,a,key,.32);brand(c,core.x,core.y,150);
    },.9,50);
    footer(c,4,a,'能力口径来自当前 schema 与 MCP 工具定义');
  }
  function control(c,t,key='A') {
    const a=K.themes[key];background(c,32+t,key);
    title(c,t,a,'04 / 有边界，可恢复','每一步，都有掌控。','有预算，有断点。长流程可以停止，也可以继续。');
    const labs=[['TIMEOUT','超时'],['RETRY','重试'],['BUDGET','预算'],['CHECKPOINT','断点'],['STOP','停止']];
    labs.forEach((lab,i)=>enter(c,t,1.1+i*.12,()=>{
      const x=112+i*344;panel(c,x,445,320,112,a,{r:20});text(c,lab[0],x+24,481,15,a.dim,500,'left',true);text(c,lab[1],x+24,528,31,a.ink,800);
      c.beginPath();c.arc(x+279,505,5+Math.sin(t*.85+i)*1.1,0,Math.PI*2);c.fillStyle=i===4?a.orange:a.teal;c.fill();
    },.7,30));
    enter(c,t,1.6,()=>{
      panel(c,112,597,1696,257,a);
      const xs=[280,670,1070,1640],y=695;
      c.strokeStyle=a.line;c.lineWidth=3;c.beginPath();c.moveTo(xs[0],y);c.lineTo(xs[3],y);c.stroke();
      // Progress pauses at the checkpoint, then resumes explicitly.
      let progress=t<3.45?mix(xs[0],xs[2],ease((t-1.8)/1.65)):t<5.15?xs[2]:mix(xs[2],xs[3],smooth((t-5.15)/1.75));
      c.strokeStyle=a.teal;c.beginPath();c.moveTo(xs[0],y);c.lineTo(progress,y);c.stroke();
      xs.forEach((x,i)=>{c.beginPath();c.arc(x,y,i===2?12:8,0,Math.PI*2);c.fillStyle=x<=progress?a.teal:a.card;c.fill();c.strokeStyle=x<=progress?a.teal:a.dim;c.lineWidth=2;c.stroke();text(c,['发现','筛选','checkpoint','继续'][i],x,y+51,i===2?22:25,a.ink,500,'center',i===2);});
      const paused=t>=3.45&&t<5.15;
      enter(c,t,2.5,()=>{pill(c,paused?'停止 · 已保存断点':t>=5.15?'用户显式恢复':'有序推进',1258,627,275,a,paused?a.orange:a.teal);},.6,15);
      if(paused){glow(c,xs[2],y,28,a.orange,.18+.05*Math.sin(t*2));}
      else{packet(c,t,1.85,xs[0],y,progress,y,a.teal,1.8);}
      text(c,'遇平台风险即停止并保存断点，风险解除后由用户显式恢复。',174,809,26,a.dim,500);
    },.9,50);
    footer(c,5,a,'流程画面为示意 · timeout / retry / budget / checkpoint / stop');
  }
  function outro(c,t,key='A') {
    const a=K.themes[key];background(c,40+t,key);
    enter(c,t,.55,()=>{orbits(c,t+8,960,360,a,key,.61);brand(c,960,360+Math.sin(t*.73)*4,202);},1.1,30);
    enter(c,t,.8,()=>text(c,'boss-agent-cli',960,536,74,a.ink,800,'center'),.9,30);
    enter(c,t,1.1,()=>text(c,'下一次机会，从这里开始。',960,618,44,a.ink,800,'center'),.9,25);
    enter(c,t,1.5,()=>{
      panel(c,487,679,946,97,a,{r:24});text(c,'›',525,741,33,a.teal,600);
      const cmd='uv tool install boss-agent-cli';text(c,cmd,569,741,32,a.ink,500,'left',true);
      c.save();c.globalAlpha=.3+.4*Math.pow(Math.sin(t*2.7),2);c.fillStyle=a.teal;c.fillRect(1184,714,3,31);c.restore();
    },.9,30);
    enter(c,t,1.85,()=>text(c,'github.com/can4hou6joeng4/boss-agent-cli',960,835,26,a.dim,500,'center',true),.75,16);
    text(c,'OPEN SOURCE  /  MIT',112,903,18,a.dim,500,'left',true);text(c,'06 / 06',1816,903,18,a.dim,500,'right',true);
  }
  return {hero:K.hero,search,roles,agent,control,outro};
})();
