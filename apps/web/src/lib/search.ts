/** Search key: ø -> o, æ -> ae, å -> a in both cases, then NFD without diacritics, so
 * "Bjorvika" finds "Bjørvika". Computed once per station, not per keystroke. */
export function fold(text: string): string {
  return text
    .toLowerCase()
    .replaceAll("ø", "o")
    .replaceAll("æ", "ae")
    .replaceAll("å", "a")
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .trim();
}

export const byName = new Intl.Collator("nb");
