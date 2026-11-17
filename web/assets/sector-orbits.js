/* Editorial navigation: size is decorative, financial values stay untouched. */
export function layoutSectorOrbits(field,nodes,sectors){
  field.classList.add('is-orbit-map');
  const width=field.clientWidth, mobile=width<480;
  const positions=[[.55,.51,.43],[.22,.22,.29],[.82,.16,.26],[.20,.76,.30],[.82,.83,.30]];
  const height=field.clientHeight;
  sectors.forEach((sector,i)=>{
    const node=nodes.children[i];
    node.querySelector('.sector-name').textContent=sector.name.replace('금융','금융\u200b');
    const p=positions[i]||[.25+(i%2)*.5,.3+Math.floor(i/2)*.25,.25];
    const diameter=width*p[2];
    const x=width*p[0],y=height*p[1],tipWidth=Math.min(280,width-16);
    node.style.setProperty('--size',diameter+'px');
    node.style.setProperty('--orbit-index',i);
    node.style.left=x+'px';node.style.top=y+'px';
    node.style.setProperty('--tip-width',tipWidth+'px');
    node.style.setProperty('--tip-left',(Math.max(8,Math.min(x-tipWidth/2,width-tipWidth-8))-x+diameter/2)+'px');
    node.classList.toggle('tip-above',p[1]>.55);
    node.classList.toggle('orbit-small',mobile&&i!==0);
    node.setAttribute('aria-label',sector.name+' 산업 분석, 지수 수익률 '+node.querySelector('.sector-return').textContent);
    if(!node.querySelector('.orbit-arrow')){
      const arrow=document.createElement('span');arrow.className='orbit-arrow';arrow.textContent='↗';arrow.setAttribute('aria-hidden','true');node.append(arrow);
    }
  });
}
