/** Three fixed, heavily blurred color orbs — the canvas's ambient light system. */
export function AmbientOrbs() {
  return (
    <>
      <div
        className="orb"
        style={{
          width: 480,
          height: 480,
          background: '#6750a4',
          top: -120,
          left: -120,
          opacity: 0.4,
        }}
      />
      <div
        className="orb"
        style={{
          width: 380,
          height: 380,
          background: '#ffb4ab',
          bottom: '8%',
          right: -60,
          opacity: 0.3,
        }}
      />
      <div
        className="orb"
        style={{
          width: 320,
          height: 320,
          background: '#cfbcff',
          top: '38%',
          left: '28%',
          opacity: 0.35,
        }}
      />
    </>
  )
}
