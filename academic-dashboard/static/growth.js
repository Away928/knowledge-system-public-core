/* Academic Dashboard v2: optional quests and reviewed XP.
   This script reads local UI state only; no capture or learning-workflow hooks. */
const subjectLabel = id => (state.subjects.find(s=>s.id===id)||{}).name || id;
const xpKind = id => state.quest_kinds[id] || {name:id,xp:0};
const growBtn = (label, handler, klass='secondary') => {
  const b=el('button',klass,label);b.type='button';b.onclick=handler;return b;
};
async function doGrowth(path,payload,success){
  try{await api(path,'POST',payload);msg(success);await refresh()}
  catch(err){msg(err.message,true)}
}
function inputHint(placeholder,multiline=false){
  const a=el(multiline?'textarea':'input');a.placeholder=placeholder;return a;
}
function getEvidence(source,ref,summary){
  return {source:source.value,reference:ref.value,summary:summary.value};
}
function evidenceEditor(target){
  const source=el('select'),ref=inputHint('证据路径 / URL / 记录 ID'),summary=inputHint('具体完成情况与可复核结论',true);
  [['learning-hub','Learning Hub'],['cognitive-engine','Cognitive Engine'],['obsidian-vault','Obsidian Vault'],['external','外部证据']].forEach(([v,t])=>{
    const option=el('option','',t);option.value=v;source.append(option);
  });
  target.append(el('label','', '成果来源'),source,el('label','', '原始证据引用'),ref,el('label','','成果描述'),summary);
  return ()=>getEvidence(source,ref,summary);
}
function reviewEditor(target,route){
  const reviewer=inputHint('审核人'),note=inputHint('审核理由；须核实成果的真实性、独立性',true);
  target.append(el('label','','审核人'),reviewer,el('label','','审核理由'),note);
  const checks={},options=[
   ['provenance','已核实原始证据来源'],
   ['independence','已核实独立完成与实际贡献'],
   ['originality','已核实未在该学科重复获得奖励']
  ];
  options.forEach(([k,text])=>{
    const lbl=el('label','check'),c=el('input');c.type='checkbox';checks[k]=c;
    lbl.append(c,el('span','',text));target.append(lbl);
  });
  const actions=el('div','actions');
  [['approved','验证完成并奖励 XP','primary'],['rejected','证据不充分','secondary']].forEach(([outcome,label,cls])=>{
    actions.append(growBtn(label,()=>doGrowth(route,{
      outcome,reviewer:reviewer.value,note:note.value,
      checks:Object.fromEntries(Object.entries(checks).map(([k,c])=>[k,c.checked]))
    },'审核已记录，经验值已根据当前阶段限制处理。'),cls));
  });
  target.append(actions);
}
function renderQuestList(){
 const wrapper=$('quest-list');wrapper.replaceChildren();
 const matching=state.quests.filter(q=>q.subject===selected);
 if(!matching.length){wrapper.append(el('p','muted','暂无任务。可先由任务发布者明确目标、完成标准和奖励；不会自动布置每日任务。'));return}
 matching.forEach(q=>{
   const card=el('div','record'),head=el('div','record-head');
   head.append(el('strong','',q.title),el('span','tag','+'+q.xp+' XP · '+xpKind(q.kind).name));card.append(head);
   card.append(el('p','',q.description),el('p','muted','核验标准：'+q.criterion));
   const labels={published:'可领取',accepted:'进行中',submitted:'待审核',completed:'已完成',rejected:'需修订'};
   card.append(el('p','muted','状态：'+labels[q.status]));
   if(q.evidence)card.append(el('p','muted',q.evidence.source+' / '+q.evidence.reference+'：'+q.evidence.summary));
   if(q.status==='published'){
     card.append(growBtn('领取任务',()=>doGrowth('/api/quests/'+q.id+'/accept',{},'任务已领取。'),'primary'));
   }else if(q.status==='accepted'||q.status==='rejected'){
     const read=evidenceEditor(card);
     const actions=el('div','actions');
     actions.append(growBtn('提交成果待审核',()=>doGrowth('/api/quests/'+q.id+'/submit',{evidence:read()},'成果已提交，待独立审核。'),'primary'));
     if(q.status==='accepted')actions.append(growBtn('放弃任务',()=>doGrowth('/api/quests/'+q.id+'/abandon',{},'任务已退回任务板。')));
     card.append(actions);
   }else if(q.status==='submitted')reviewEditor(card,'/api/quests/'+q.id+'/review');
   wrapper.append(card);
 });
}
function renderAchievements(){
 const wrapper=$('achievement-list');wrapper.replaceChildren();
 const entries=state.achievements.filter(a=>a.subject===selected);
 if(!entries.length){wrapper.append(el('p','muted','暂无自主成果申请。'));return}
 entries.forEach(a=>{
   const card=el('div','record'),head=el('div','record-head');
   head.append(el('strong','',a.title),el('span','tag','+'+a.xp+' XP · '+xpKind(a.kind).name));card.append(head);
   const status={submitted:'待审核',completed:'已确认',rejected:'不通过'}[a.status];
   card.append(el('p','muted',status+' · '+a.evidence.source+' / '+a.evidence.reference));
   card.append(el('p','',a.evidence.summary));
   if(a.status==='submitted')reviewEditor(card,'/api/achievements/'+a.id+'/review');
   wrapper.append(card);
 });
}
function renderGrowth(){
 if(!$('xp-status'))return;
 const s=state.subjects.find(x=>x.id===selected),p=s.progress;
 const bar=$('xp-bar'),txt=$('xp-text'),status=$('xp-status');
 if(s.level===null){
  txt.textContent='—';status.textContent='尚未定级：可先申请初始定级，后续成果才能进入学科 XP 账本。';
  bar.style.width='0%';
 }else if(p.reason==='max_level'){
  txt.textContent='最高等级';status.textContent='Lv.7 已是最高级。学习与研究可继续，不再开放晋级。';bar.style.width='100%';
 }else{
  txt.textContent=p.required_xp===null?(p.xp+' XP / 阈值未设'):(p.xp+' / '+p.required_xp+' XP');
  bar.style.width=p.required_xp===null?'0%':Math.min(100,p.xp/p.required_xp*100)+'%';
  status.textContent=p.unlocked?'经验已满：晋级考核已解锁，但不会自动启动。':
    p.reason==='threshold_unset'?'已获 XP 会保留。请先配置当前阶段所需 XP，再决定是否开放晋级。':
    '继续完成经过独立核验的任务或自主成果，累积经验值。';
 }
 const currentLevel=s.level, target=$('target');
 const locked=currentLevel!==null&&(!p.unlocked||currentLevel>=7);
 target.disabled=currentLevel!==null;
 if(currentLevel!==null&&currentLevel<7)target.value=String(currentLevel+1);
 $('assessment-hint').textContent=currentLevel===null?'初次使用允许直接申请相应学科的定级考核，不需要先刷 XP。':
  currentLevel>=7?'已达到最高等级。':
  p.unlocked?'Lv.'+(currentLevel+1)+' 晋级考核已解锁。考核失败保持 XP 满格。':
  '晋级考核暂未解锁：'+(p.reason==='threshold_unset'?'须先配置经验阈值。':'须先将经验值攒满。');
 $('assessment-submit').disabled=locked;
 const policyLevel=$('policy-level'),preserved=policyLevel.value;policyLevel.replaceChildren();
 state.levels.filter(l=>l.level<7).forEach(l=>{
   const option=el('option','', 'Lv.'+l.level+' → Lv.'+(l.level+1));option.value=l.level;policyLevel.append(option);
 });
 policyLevel.value=preserved||String(Math.min(currentLevel||1,6));
 if(!policyLevel.value)policyLevel.value='1';
 const threshold=state.xp_requirements[policyLevel.value];
 $('threshold-existing').textContent=threshold===undefined?'该阶段尚未设置经验阈值。':'现有试行阈值：'+threshold+' XP。';
 renderQuestList();renderAchievements();
 const awards=$('award-list');awards.replaceChildren();
 const entries=state.awards.filter(a=>a.subject===selected).slice(0,8);
 if(!entries.length){awards.append(el('p','muted','暂无已审核发放的经验。'));return}
 entries.forEach(a=>{
   awards.append(el('div','record',a.evidence.reference+' · +'+a.applied_xp+' XP'+
     (a.applied_xp<a.requested_xp?'（经验封顶，原奖励 '+a.requested_xp+'）':'')));
 });
}
$('policy-level').addEventListener('change',()=>{
 const val=state?.xp_requirements?.[$('policy-level').value];
 $('threshold-existing').textContent=val===undefined?'该阶段尚未设置经验阈值。':'现有试行阈值：'+val+' XP。';
});
$('xp-policy').onsubmit=async e=>{
 e.preventDefault();await doGrowth('/api/xp-policy',{
   level:Number($('policy-level').value),required_xp:Number($('required-xp').value),
   rationale:$('policy-rationale').value
 },'试行 XP 阈值已配置（非经校准的正式标准）。');
};
$('quest-form').onsubmit=async e=>{
 e.preventDefault();await doGrowth('/api/quests',{
   subject:selected,kind:$('quest-kind').value,title:$('quest-title').value,
   description:$('quest-description').value,criterion:$('quest-criterion').value
 },'任务已发布，可选择领取。');
};
$('achievement-form').onsubmit=async e=>{
 e.preventDefault();await doGrowth('/api/achievements',{
   subject:selected,kind:$('achievement-kind').value,title:$('achievement-title').value,
   evidence:{source:$('achievement-source').value,reference:$('achievement-ref').value,summary:$('achievement-summary').value}
 },'自主成果已提交待审核，没有自动发放 XP。');
};
