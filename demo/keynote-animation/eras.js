window.BPM = 120;
window.PUNCH = 0;
window.ERAS = ['hero','search','roles','agent','control','outro'].map((id,i)=>({
  id, dur:8, t0:i*8, t1:(i+1)*8,
  ...(i?{transition:{type:'lightSweep',dur:.65}}:{}),
}));
