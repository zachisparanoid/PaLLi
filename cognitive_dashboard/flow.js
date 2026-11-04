'use strict';
// Connections are recorded parent-child nesting only; no chronological guessing.
const flowState={mode:'live',trace:'',position:0,playing:false,timer:null,selected:null,lastSeen:new Set(),initialized:false,queue:[],liveTimer:null,lastLiveTime:null,skipped:0,messages:[],messageError:''};
const svgNS='http://www.w3.org/2000/svg';
function svg(tag,attrs={}){let x=document.createElementNS(svgNS,tag);for(const [k,v] of Object.entries(attrs))x.setAttribute(k,String(v));return x}
function flowColumn(name){
 if(['turn','conversation','first_look','recall','reflection','night_cycle','proactive','arbiter'].includes(name))return 0;
 if(['facts','inner_memory','emotional_memory','stakes','fears','wants','monologue','daydream','relationship','tonight'].includes(name))return 1;
 return 2;
}
function flowStop(){flowState.playing=false;if(flowState.timer)clearInterval(flowState.timer);flowState.timer=null}
function flowReplay(events){flowStop();flowClearLive();flowState.mode='replay';flowState.trace=events[0]?.[0]||'';flowState.position=0;flowState.selected=null;render()}
function renderFlow(el,events){
 const traces=groups(events); let trace=traces.find(x=>x[0]===flowState.trace);
 if(flowState.mode==='replay'&&!trace){flowStop();flowState.trace=traces[0]?.[0]||'';trace=traces.find(x=>x[0]===flowState.trace);flowState.position=0}
 const source=flowState.mode==='replay'?[...(trace?.[1]||[])].sort((a,b)=>a.ts-b.ts):events.slice(-350);
 const shown=flowState.mode==='replay'?source.slice(0,flowState.position):source;
 let toolbar=node('div','flow-toolbar');
 let mode=button(flowState.mode==='live'?'● LIVE':'○ GO LIVE',()=>{flowStop();flowClearLive();flowState.mode='live';flowState.selected=null;render()},'flow-control'+(flowState.mode==='live'?' active':''));
 let replay=button('↺ Replay a trace',()=>flowReplay(traces),'flow-control'+(flowState.mode==='replay'?' active':''));
 let select=node('select','flow-select');select.setAttribute('aria-label','Trace to replay');
 if(!traces.length)select.add(new Option('No traces available',''));
 for(const [id,es] of traces.slice(0,100))select.add(new Option(`${time(es[0].ts)} · ${id.slice(0,10)} · ${es.length} events`,id));
 select.value=flowState.trace;select.disabled=flowState.mode!=='replay'||!traces.length;
 select.onchange=()=>{flowStop();flowState.trace=select.value;flowState.position=0;flowState.selected=null;render()};
 add(toolbar,mode,replay,select);el.append(toolbar);
 if(flowState.mode==='replay'){
  let controls=node('div','flow-replay');let play=button(flowState.playing?'❚❚ Pause':'▶ Play',()=>{if(flowState.playing){flowStop();render();return}if(flowState.position>=source.length)flowState.position=0;flowState.playing=true;render();flowReplayNext(source)},'flow-control');play.disabled=!source.length;
  let step=button('Step →',()=>{flowStop();flowState.position=Math.min(source.length,flowState.position+1);render()},'flow-control');step.disabled=flowState.position>=source.length;
  let range=node('input','flow-range');range.type='range';range.min=0;range.max=source.length;range.value=flowState.position;range.setAttribute('aria-label','Replay position');range.oninput=()=>{flowStop();flowState.position=Number(range.value);render()};
  add(controls,play,step,range,node('span','flow-count',`${flowState.position} / ${source.length}`));el.append(controls);el.append(node('div','flow-replay-note','Recorded order · gaps adjusted for readability'));
 }
 let layout=node('div','flow-layout'),panel=node('section','flow-canvas card'),head=node('div','flow-canvas-head');
 add(head,node('div','flow-canvas-title',flowState.mode==='live'?'LIVE ACTIVITY':'TRACE REPLAY'),node('div','flow-caption','Lines show recorded execution nesting, not proven influence'));panel.append(head);if(flowState.mode==='live')panel.append(node('div','flow-timing',flowTimingText()));
 let graph=node('div','flow-scroll');let names=[...new Set([...(flowState.mode==='live'?['web_search']:[]),...(flowState.mode==='replay'?source:shown).filter(e=>e.organ&&e.organ!=='tracing').map(e=>e.organ)])].sort((a,b)=>flowColumn(a)-flowColumn(b)||a.localeCompare(b));
 if(!names.length){graph.append(node('div','flow-empty',flowState.mode==='replay'?'Choose a trace to replay.':'Waiting for recorded activity. Enable tracing to see faculties appear.'))}else{
  let cols=[[],[],[]];names.forEach(n=>cols[flowColumn(n)].push(n));let height=Math.max(380,Math.max(...cols.map(c=>c.length))*76+60),width=900;
  let positions=new Map();cols.forEach((col,i)=>col.forEach((n,j)=>positions.set(n,{x:145+i*305,y:(j+1)*height/(col.length+1)})));
  let drawing=svg('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'Observed faculty activity and recorded execution nesting'});drawing.classList.add('flow-graph');
  let index=new Map(shown.map(e=>[e.id,e])), edges=new Map();
  for(const e of shown){let parent=index.get(e.parent_id);if(!parent||parent.organ===e.organ||!positions.has(parent.organ)||!positions.has(e.organ))continue;let key=parent.organ+'\0'+e.organ;if(!edges.has(key))edges.set(key,[]);edges.get(key).push(e)}
  for(const [key,items] of edges){let [from,to]=key.split('\0'),a=positions.get(from),b=positions.get(to),mx=(a.x+b.x)/2;let path=svg('path',{d:`M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`,class:'flow-edge',tabindex:'0',role:'button','aria-label':`${nice(from)} to ${nice(to)}: execution nesting, ${items.length} observations`});path.dataset.from=from;path.dataset.to=to;let last=items.at(-1);path.onclick=()=>{flowState.selected=last;render()};path.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();path.onclick()}};drawing.append(path);
   if(flowState.mode==='replay'&&shown.at(-1)?.id===last.id){let animated=svg('path',{d:path.getAttribute('d'),class:'flow-edge-pulse'});drawing.append(animated)}
  }
  for(const name of names){let pos=positions.get(name),activity=shown.filter(e=>e.organ===name),last=activity.at(-1),lit=last&&flowState.mode==='replay'&&shown.at(-1)?.id===last.id;let g=svg('g',{class:'flow-node'+(lit?' lit':'')+(flowState.selected?.organ===name?' selected':''),transform:`translate(${pos.x-105} ${pos.y-25})`,tabindex:'0',role:'button','aria-label':`${nice(name)}, ${activity.length} observations`});
   g.dataset.organ=name;g.append(svg('rect',{width:210,height:50,rx:11}));let dot=svg('circle',{cx:19,cy:25,r:4});g.append(dot);let title=svg('text',{x:32,y:22,class:'flow-node-title'});title.textContent=nice(name);g.append(title);let sub=svg('text',{x:32,y:38,class:'flow-node-sub'});sub.textContent=activity.length?`${activity.length} observed · ${nice(last.evidence)}`:'waiting';g.append(sub);g.onclick=()=>{flowState.selected=[...activity].reverse().find(e=>e.action!=='start'&&e.action!=='finish')||last||null;render()};g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();g.onclick()}};drawing.append(g)
  }graph.append(drawing)
 }panel.append(graph);panel.append(node('p','flow-foot','Only observed faculties and parent links are shown. Missing paths may be uninstrumented; a pulse is not proof of causation.'));layout.append(panel);
 let detail=node('aside','card flow-detail');let e=flowState.selected;add(detail,node('div','card-kicker','SELECTED OBSERVATION'));
 if(e){add(detail,node('h2','',nice(e.organ)),node('p','flow-detail-action',nice(e.action)),pill(e),node('p','flow-detail-copy',`${time(e.ts)} · ${nice(e.stage)}${e.outcome?' · '+nice(e.outcome):''}${e.organ==='web_search'&&e.action==='results_returned'&&Number.isInteger(e.count)?' · '+e.count+' results':''}`));if(e.changed!==undefined)detail.append(node('p','flow-detail-copy','Comparison changed: '+(e.changed?'yes':'no')));if(e.trace_id)detail.append(button('Open full trace →',()=>{state.trace=e.trace_id;setView('traces')},'flow-link'));if(e.entity)detail.append(button('Follow this state →',()=>{state.entity=e.entity;setView('entities')},'flow-link'));detail.append(node('p','flow-hint','Parent links indicate execution nesting. Evidence labels describe what was observed, not automatically what caused an outcome.'))}else{detail.append(node('p','flow-hint','Select a faculty or connection to inspect its latest recorded observation.'))}layout.append(detail);el.append(layout);
 let feed=node('section','card conversation-feed'),fh=node('div','flow-feed-head');add(fh,node('div','flow-canvas-title','CONVERSATIONS · SINCE DASHBOARD START'),node('span','flow-caption','Stored messages update with the live view'));feed.append(fh);
 if(flowState.messageError)feed.append(node('div','flow-empty',flowState.messageError));else if(!flowState.messages.length)feed.append(node('div','flow-empty','Waiting for newly stored conversation messages.'));
 for(const m of flowState.messages.slice(-30)){let row=node('div','message-row');add(row,node('div','message-meta',`${m.username||m.role} · ${m.platform} · ${time(m.timestamp)}`),node('div','message-content',m.content));feed.append(row)}
 feed.append(node('p','flow-foot','Conversation text is read from the local database and is visible to anyone with dashboard access. Messages are not automatically matched to traces.'));el.append(feed);
}
// The collector polls in batches. Animate new events one at a time in recorded
// timestamp order; bounded, adjusted gaps keep bursts legible and near live.
function flowReplayNext(source){
 if(!flowState.playing||state.view!=='flow'||flowState.mode!=='replay'){flowStop();return}
 flowState.position=Math.min(source.length,flowState.position+1);if(flowState.position>=source.length){flowStop();render();return}render();
 const gap=Math.max(0,(Number(source[flowState.position].ts)-Number(source[flowState.position-1].ts))*1000)||0;
 flowState.timer=setTimeout(()=>flowReplayNext(source),Math.min(1500,Math.max(180,gap)))
}
function flowTimingText(){return 'Ordered live events · timing adjusted for readability'+(flowState.skipped?` · ${flowState.skipped} animations skipped to catch up`:'')}
function flowClearLive(){if(flowState.liveTimer)clearTimeout(flowState.liveTimer);flowState.liveTimer=null;flowState.queue=[];flowState.lastLiveTime=null}
function flowAnimate(event){
 const graph=document.querySelector('.flow-graph');if(!graph)return;
 graph.querySelectorAll('.flow-node.lit').forEach(n=>n.classList.remove('lit'));
 graph.querySelectorAll('.flow-edge-pulse').forEach(n=>n.remove());
 const n=[...graph.querySelectorAll('.flow-node')].find(n=>n.dataset.organ===event.organ);
 if(n){void n.getBoundingClientRect();n.classList.add('lit')}
 const parent=state.events.find(e=>e.id===event.parent_id);
 if(parent&&parent.organ!==event.organ){
  const edge=[...graph.querySelectorAll('.flow-edge')].find(p=>p.dataset.from===parent.organ&&p.dataset.to===event.organ);
  if(edge){let pulse=svg('path',{d:edge.getAttribute('d'),class:'flow-edge-pulse'});edge.after(pulse)}
 }
 const status=document.querySelector('.flow-timing');if(status)status.textContent=flowTimingText();
}
function flowDrain(){
 flowState.liveTimer=null;
 if(state.view!=='flow'||flowState.mode!=='live'){flowClearLive();return}
 const event=flowState.queue.shift();if(!event)return;
 flowAnimate(event);
 if(flowState.queue.length){
  const next=flowState.queue[0],gap=Math.max(0,(Number(next.ts)-Number(event.ts))*1000)||0;
  const delay=flowState.queue.length>20?Math.min(150,Math.max(50,gap)):Math.min(700,Math.max(180,gap));
  flowState.liveTimer=setTimeout(flowDrain,delay);
 }else{flowState.liveTimer=setTimeout(()=>{flowState.liveTimer=null;flowState.lastLiveTime=null;if(flowState.queue.length)flowDrain()},180)}
}
function flowObserve(events){
 const fresh=flowState.initialized&&state.view==='flow'&&flowState.mode==='live'?events.filter(e=>!flowState.lastSeen.has(e.id)&&(!state.run||e.run===state.run)&&e.organ&&e.organ!=='tracing'):[];
 flowState.lastSeen=new Set(events.map(e=>e.id));flowState.initialized=true;
 if(!fresh.length)return;
 fresh.sort((a,b)=>a.ts-b.ts);flowState.queue.push(...fresh);flowState.queue.sort((a,b)=>a.ts-b.ts);
 // Overload is disclosed; the events remain in timeline and trace history.
 if(flowState.queue.length>120){flowState.skipped+=flowState.queue.length-120;flowState.queue.splice(0,flowState.queue.length-120)}
}
function flowKick(){if(flowState.queue.length&&!flowState.liveTimer)flowState.liveTimer=setTimeout(flowDrain,0)}
async function loadConversations(){try{let r=await fetch('/api/conversations',{cache:'no-store'});if(!r.ok)throw Error();let data=await r.json();flowState.messages=data.messages;flowState.messageError=''}catch(_){flowState.messageError='Conversation feed unavailable; check the configured database.'}}
