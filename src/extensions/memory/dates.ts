/** Local-timezone YYYY-MM-DD — memory filenames follow the user's day, not UTC. */
export const localIsoDate = (date: Date = new Date()): string => {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");

  return `${date.getFullYear()}-${month}-${day}`;
};
