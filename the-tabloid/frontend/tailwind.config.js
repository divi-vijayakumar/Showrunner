/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  safelist: [
    // Persona + channel accents composed dynamically via template strings.
    {
      pattern:
        /^(bg|border|border-t|border-l|text|shadow|from|to)-(indigo|red|emerald|amber|purple|cyan|pink|fuchsia|orange)-(300|400|500|600)(\/(10|20|30|40|50|60|70|80|90))?$/,
    },
  ],
  theme: {
    extend: {
      colors: {
        canvas: '#050208',
        surface: '#141218',
        'surface-container-lowest': '#0f0d13',
        'surface-container-low': '#1d1b20',
        'surface-container': '#211f24',
        'surface-container-high': '#2b292f',
        'surface-container-highest': '#36343a',
        'on-surface': '#e6e0e9',
        'on-surface-variant': '#cbc4d2',
        outline: '#948e9c',
        'outline-variant': '#494551',
        primary: '#cfbcff',
        'primary-container': '#6750a4',
        // Persona accents (kept separate for clarity)
        anchor: '#6366f1',       // indigo-500
        provocateur: '#ef4444',  // red-500
        analyst: '#10b981',      // emerald-500
        humanist: '#f59e0b',     // amber-500
      },
      fontFamily: {
        display: ['Epilogue', 'system-ui', 'sans-serif'],
        heading: ['Epilogue', 'system-ui', 'sans-serif'],
        body: ['Work Sans', 'system-ui', 'sans-serif'],
        mono: ['Space Grotesk', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        'display-xl': ['4.5rem', { lineHeight: '1.1', letterSpacing: '-0.02em', fontWeight: '800' }],
        h1: ['3rem', { lineHeight: '1.2', fontWeight: '700' }],
        h2: ['2.25rem', { lineHeight: '1.2', fontWeight: '700' }],
        'body-lg': ['1.125rem', { lineHeight: '1.6', fontWeight: '400' }],
        'body-md': ['1rem', { lineHeight: '1.6', fontWeight: '400' }],
        mono: ['0.75rem', { lineHeight: '1.4', letterSpacing: '0.05em', fontWeight: '500' }],
      },
      borderRadius: {
        DEFAULT: '0.5rem',
        lg: '0.75rem',
        xl: '1rem',
        '2xl': '1.5rem',
      },
      keyframes: {
        ticker: {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(-100%)' },
        },
        pulseDot: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.35' },
        },
      },
      animation: {
        ticker: 'ticker 22s linear infinite',
        pulseDot: 'pulseDot 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
