import fs from "node:fs/promises";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const OUT = "C:/apps/myllm-3/Docs/Frodo_State_Checkpointer_Store_IDs.pptx";
const QA = "C:/apps/myllm-3/Docs/_build/rendered";
const W = 1280, H = 720;
const C = { ink: "#1A1C1B", muted: "#6F675F", paper: "#FAF9F7", low: "#F0ECE7", line: "#D4C4B7", brown: "#79542E", sand: "#F9DFB9", green: "#668B5E", blue: "#DDEBF3" };

function tb(slide, text, x, y, w, h, size=24, opts={}) {
  const s = slide.shapes.add({ geometry:"textbox", position:{left:x,top:y,width:w,height:h}, fill:"none", line:{style:"solid",fill:"none",width:0} });
  s.text = text;
  s.text.style = { fontSize:size, typeface:"Malgun Gothic", color:opts.color||C.ink, bold:!!opts.bold, alignment:opts.align||"left", verticalAlignment:opts.valign||"top", autoFit:opts.autoFit||"shrinkText" };
  return s;
}
function box(slide, x,y,w,h, fill=C.low, radius="rounded-xl", line=C.line) {
  return slide.shapes.add({geometry:"roundRect",position:{left:x,top:y,width:w,height:h},fill,line:{style:"solid",fill:line,width:1},borderRadius:radius});
}
function line(slide,x,y,w,h=0,color=C.ink,width=2) {
  return slide.shapes.add({geometry:"straightConnector1",position:{left:x,top:y,width:w,height:h},fill:"none",line:{style:"solid",fill:color,width}});
}
function base(slide, n, title) {
  slide.background.fill=C.paper;
  tb(slide,title,42,34,1160,84,39,{bold:true});
  tb(slide,String(n).padStart(2,"0"),1182,662,56,22,13,{color:C.muted,align:"right"});
}
function notes(slide, refs) { slide.speakerNotes.textFrame.setText(`[Sources]\n${refs.map(r=>`- ${r}`).join("\n")}`); }

const deck = Presentation.create({slideSize:{width:W,height:H}});

// 1 — Codex Grid slide-01 silhouette: minimal title.
{
  const s=deck.slides.add(); s.background.fill=C.paper;
  tb(s,"FRODO · LANGGRAPH MEMORY MODEL",42,42,760,48,22,{bold:true,color:C.brown});
  tb(s,"State, Checkpointer,\nStore와 ID의 관계",42,182,1000,220,66,{bold:true});
  tb(s,"이 프로젝트의 실제 데이터 흐름으로 이해하기",42,510,720,70,28,{color:C.muted});
  box(s,1090,42,145,145,C.brown,"rounded-2xl",C.brown); tb(s,"S\nC\nS",1120,60,90,112,26,{bold:true,color:"#FFFFFF",align:"center"});
  notes(s,["C:/apps/myllm-3/AI.md §17"]);
}

// 2 — Codex Grid slide-06 silhouette: three-column mental model.
{
  const s=deck.slides.add(); base(s,2,"세 구성요소는 ‘범위’와 ‘구분 키’가 다르다");
  const xs=[42,453,864], fills=[C.sand,C.blue,"#E5EBDD"];
  const data=[
    ["STATE","지금 처리 중인 생각","Graph 노드 사이의 작업 데이터\nmessages · question · tools · documents\n현재 실행에서 읽고 갱신"],
    ["CHECKPOINTER","현재 대화방의 기억","Graph State 스냅샷과 실행 지점\n같은 thread_id에서 복원\nPostgresSaver가 영속화"],
    ["STORE","사용자의 장기 기억","대화방을 넘어 재사용할 정보\n(\"memories\", user_id)로 격리\nPostgresStore가 저장·조회"]
  ];
  data.forEach((d,i)=>{ box(s,xs[i],170,374,430,fills[i],"rounded-2xl",fills[i]); tb(s,d[0],xs[i]+28,196,318,34,20,{bold:true,color:C.brown}); tb(s,d[1],xs[i]+28,250,318,78,30,{bold:true}); tb(s,d[2],xs[i]+28,362,318,178,21,{color:C.muted}); });
  notes(s,["C:/apps/myllm-3/AI.md §17","C:/apps/myllm-3/api/app/graph.py:22","C:/apps/myllm-3/api/app/conversation_store.py:35"]);
}

// 3 — relationship diagram, connectors first.
{
  const s=deck.slides.add(); base(s,3,"State는 흐르고, Checkpointer는 되살리고, Store는 건너간다");
  line(s,395,322,90,0,C.brown,3); line(s,795,322,90,0,C.brown,3);
  line(s,640,468,0,88,C.green,3);
  box(s,80,210,315,225,C.sand,"rounded-2xl",C.sand); box(s,485,210,310,225,C.blue,"rounded-2xl",C.blue); box(s,885,210,315,225,"#E5EBDD","rounded-2xl","#E5EBDD");
  tb(s,"① 요청",110,238,250,35,21,{bold:true,color:C.brown}); tb(s,"초기 State",110,286,250,45,31,{bold:true}); tb(s,"현재 질문 + 새 HumanMessage\nStore에서 조회한 memories",110,350,250,62,18,{color:C.muted});
  tb(s,"② Graph 실행",515,238,250,35,21,{bold:true,color:C.brown}); tb(s,"State 갱신",515,286,250,45,31,{bold:true}); tb(s,"노드가 messages · routing ·\ndocuments · generation을 갱신",515,350,250,62,18,{color:C.muted});
  tb(s,"③ 응답 이후",915,238,250,35,21,{bold:true,color:C.brown}); tb(s,"두 저장 경로",915,286,250,45,31,{bold:true}); tb(s,"Checkpointer: 실행 문맥\nStore: 장기 사용자 사실",915,350,250,62,18,{color:C.muted});
  box(s,475,550,330,74,C.ink,"rounded-xl",C.ink); tb(s,"thread_id로 이전 체크포인트 복원",500,570,280,30,20,{bold:true,color:"#FFFFFF",align:"center"});
  notes(s,["C:/apps/myllm-3/AI.md §17-5","C:/apps/myllm-3/api/app/main.py:320-359"]);
}

// 4 — Codex Grid slide-05 silhouette: two-column identity clarification.
{
  const s=deck.slides.add(); base(s,4,"session ID와 thread ID: 이 프로젝트에서는 같은 말이 아니다");
  box(s,42,174,560,420,C.low,"rounded-2xl",C.low); box(s,678,174,560,420,"#F7EFE6","rounded-2xl","#F7EFE6");
  tb(s,"인증 세션",76,210,490,45,26,{bold:true,color:C.brown}); tb(s,"JWT access token",76,273,490,52,34,{bold:true}); tb(s,"누가 요청했는지 증명한다.\nuser_id와 대화 소유권 검증에 사용된다.\nconversation_id를 대신하지 않는다.",76,360,480,130,22,{color:C.muted});
  tb(s,"대화 실행 식별자",712,210,490,45,26,{bold:true,color:C.brown}); tb(s,"conversation_id = thread_id",712,273,490,52,34,{bold:true}); tb(s,"어느 대화방의 Graph 상태를 복원할지 정한다.\n브라우저가 UUID를 만들고 API가 thread_id로 전달한다.\n새 대화는 새 ID를 사용한다.",712,360,480,150,22,{color:C.muted});
  notes(s,["C:/apps/myllm-3/README.md:467","C:/apps/myllm-3/api/app/main.py:334","C:/apps/myllm-3/front/app.js:179-185"]);
}

// 5 — checkpoint id lifecycle timeline (Codex Grid slide-17 silhouette).
{
  const s=deck.slides.add(); base(s,5,"checkpoint_id는 thread 안에서 생성되는 ‘스냅샷 버전’이다");
  line(s,90,355,1060,0,C.ink,2);
  const xs=[110,520,930]; xs.forEach(x=>s.shapes.add({geometry:"ellipse",position:{left:x,top:345,width:20,height:20},fill:C.brown,line:{style:"solid",fill:C.brown,width:1}}));
  const stages=[
    [110,"첫 질문","thread_id = A","Graph 실행 후\ncheckpoint_id = cp-1"],
    [520,"후속 질문","thread_id = A","cp-1을 바탕으로 실행 후\ncheckpoint_id = cp-2"],
    [930,"새 대화","thread_id = B","A의 체크포인트 미복원\n새 체인 시작"]
  ];
  stages.forEach(([x,a,b,c])=>{tb(s,a,x-10,275,280,40,22,{bold:true,color:C.brown});tb(s,b,x-10,405,280,40,27,{bold:true});tb(s,c,x-10,465,280,86,20,{color:C.muted});});
  tb(s,"latest_checkpoint_id(thread_id)는 해당 thread의 최신 checkpoint_id를 화면에 보여준다",180,610,920,32,20,{bold:true,align:"center"});
  notes(s,["C:/apps/myllm-3/api/app/conversation_store.py:119-127","C:/apps/myllm-3/front/app.js:184-185","C:/apps/myllm-3/AI.md §17-2"]);
}

// 6 — scoped memory comparison.
{
  const s=deck.slides.add(); base(s,6,"thread_id는 대화를 격리하고, user_id는 장기 기억을 묶는다");
  tb(s,"USER A",52,155,160,34,20,{bold:true,color:C.brown});
  line(s,160,245,160,0,C.brown,2); line(s,160,245,0,210,C.brown,2); line(s,160,455,160,0,C.brown,2);
  box(s,320,200,330,100,C.blue,"rounded-xl",C.blue); tb(s,"THREAD A-1",345,220,270,28,20,{bold:true});tb(s,"고유 Checkpoint 체인",345,258,270,24,17,{color:C.muted});
  box(s,320,410,330,100,C.blue,"rounded-xl",C.blue); tb(s,"THREAD A-2",345,430,270,28,20,{bold:true});tb(s,"별도의 Checkpoint 체인",345,468,270,24,17,{color:C.muted});
  line(s,650,250,115,0,C.green,3); line(s,650,460,115,0,C.green,3);
  box(s,765,260,430,230,"#E5EBDD","rounded-2xl","#E5EBDD"); tb(s,"STORE NAMESPACE",800,292,360,32,20,{bold:true,color:C.green}); tb(s,"(\"memories\", user_id)",800,345,360,42,30,{bold:true}); tb(s,"두 thread 모두 같은 사용자 기억을 조회\n예: Python 선호 · Frodo 프로젝트 진행",800,415,350,55,19,{color:C.muted});
  notes(s,["C:/apps/myllm-3/api/app/memory.py:13-17","C:/apps/myllm-3/api/app/memory.py:48-51","C:/apps/myllm-3/AI.md §17-3"]);
}

// 7 — practical lookup table.
{
  const s=deck.slides.add(); base(s,7,"ID를 보면 ‘무엇을 찾는 키인지’부터 구분한다");
  const rows=[
    ["JWT / 인증 세션","사용자 인증","Bearer token","로그인 주체·user_id 확인"],
    ["conversation_id","앱의 대화방","UUID","히스토리·소유권·메시지 조회"],
    ["thread_id","LangGraph 실행 문맥","conversation_id와 동일","같은 대화의 State 복원"],
    ["checkpoint_id","특정 thread의 스냅샷","LangGraph 생성 값","최신 실행 버전 표시·추적"],
    ["user_id","장기 기억 소유자","로그인 사용자 UUID","Store namespace 격리"]
  ];
  const cols=[42,285,585,870], widths=[235,292,277,368];
  ["개념","가리키는 대상","이 프로젝트의 값","주요 역할"].forEach((t,i)=>{box(s,cols[i],158,widths[i],55,C.ink,"rounded-none",C.paper);tb(s,t,cols[i]+14,174,widths[i]-28,25,18,{bold:true,color:"#FFFFFF"});});
  rows.forEach((r,ri)=>r.forEach((t,ci)=>{const y=213+ri*78;box(s,cols[ci],y,widths[ci],78,ri%2?C.paper:C.low,"rounded-none",C.line);tb(s,t,cols[ci]+14,y+15,widths[ci]-28,50,17,{bold:ci===0,color:ci===0?C.brown:C.ink});}));
  notes(s,["C:/apps/myllm-3/api/app/main.py:105","C:/apps/myllm-3/api/app/main.py:334-359","C:/apps/myllm-3/api/app/conversation_store.py:119-127"]);
}

// 8 — Codex Grid slide-26 silhouette: synthesis.
{
  const s=deck.slides.add(); s.background.fill=C.paper;
  tb(s,"핵심 정리",42,42,240,42,23,{bold:true,color:C.brown});
  tb(s,"State는 데이터,\nthread_id는 경계,\ncheckpoint_id는 버전이다.",42,165,1100,265,62,{bold:true});
  tb(s,"Store는 user_id를 기준으로 그 경계를 넘어 장기 기억을 공유한다.",42,525,950,58,27,{color:C.muted});
  tb(s,"FRODO",1110,645,120,22,14,{bold:true,color:C.brown,align:"right"});
  notes(s,["C:/apps/myllm-3/AI.md §17"]);
}

await fs.mkdir(QA,{recursive:true});
for (const [i,s] of deck.slides.items.entries()) {
  const png=await deck.export({slide:s,format:"png",scale:1});
  await fs.writeFile(`${QA}/slide-${i+1}.png`,new Uint8Array(await png.arrayBuffer()));
  const layout=await s.export({format:"layout"});
  await fs.writeFile(`${QA}/slide-${i+1}.layout.json`,await layout.text());
}
const montage=await deck.export({format:"png",montage:true,scale:1});
await fs.writeFile("C:/apps/myllm-3/Docs/_build/montage.png",new Uint8Array(await montage.arrayBuffer()));
const pptx=await PresentationFile.exportPptx(deck); await pptx.save(OUT);
console.log(OUT);
