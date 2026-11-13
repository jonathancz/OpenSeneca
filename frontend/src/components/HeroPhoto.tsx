import { useEffect } from 'react';
import { startHeroRefresh, useHeroStore } from '../lib/hero';

/**
 * The current rotating photo, darkened into the palette. Place inside a
 * `position: relative; overflow: hidden` container. Renders nothing until a
 * photo has loaded, so the panel simply stays a dark card offline.
 */
export function HeroBackdrop({ position = 'center' }: { position?: string }) {
  const imageUrl = useHeroStore((s) => s.imageUrl);
  const photo = useHeroStore((s) => s.status?.photo);
  useEffect(() => startHeroRefresh(), []);

  return (
    <div aria-hidden="true" className="absolute inset-0 pointer-events-none">
      {imageUrl && (
        <div
          className="sn-photo"
          style={{
            backgroundImage: `url(${imageUrl})`,
            backgroundColor: photo?.color || undefined,
            backgroundPosition: position,
          }}
        />
      )}
      <div className="sn-photo-shade" />
    </div>
  );
}

const linkStyle = {
  color: 'var(--color-text-secondary)',
  textDecoration: 'underline',
  textDecorationColor: 'rgba(255,255,255,0.2)',
  textUnderlineOffset: 3,
} as const;

/** Attribution required by the Unsplash API guidelines (or the bundled credit). */
export function PhotoCredit({ className }: { className?: string }) {
  const photo = useHeroStore((s) => s.status?.photo);
  const imageUrl = useHeroStore((s) => s.imageUrl);
  if (!photo || !imageUrl || photo.source === 'none') return null;

  if (photo.source === 'unsplash') {
    return (
      <p className={`text-[11.5px] ${className ?? ''}`} style={{ color: 'var(--color-text-tertiary)' }}>
        Photo by{' '}
        <a href={photo.photographer_url} target="_blank" rel="noopener noreferrer" style={linkStyle}>
          {photo.photographer}
        </a>{' '}
        on{' '}
        <a href={photo.unsplash_url} target="_blank" rel="noopener noreferrer" style={linkStyle}>
          Unsplash
        </a>
      </p>
    );
  }

  return (
    <p className={`text-[11.5px] ${className ?? ''}`} style={{ color: 'var(--color-text-tertiary)' }}>
      Photo:{' '}
      <a href={photo.credit_url} target="_blank" rel="noopener noreferrer" style={linkStyle}>
        {photo.credit}
      </a>
      {photo.license ? `, ${photo.license.toLowerCase()}` : ''}
    </p>
  );
}
