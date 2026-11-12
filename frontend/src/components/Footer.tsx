export default function Footer() {
  const terms = import.meta.env.VITE_TERMS_URL;
  const privacy = import.meta.env.VITE_PRIVACY_URL;
  return (
    <footer className="legal muted small">
      <p>
        Football AI provides statistical estimates for information and entertainment. It is not betting, financial or professional advice,
        and past results do not guarantee future ones. Predictions can be wrong. For adults only; gamble responsibly.
      </p>
      <p>Football data provided by the Football-Data.org API.</p>
      {(terms || privacy) && (
        <p>{terms && <a href={terms}>Terms of Service</a>}{terms && privacy && " · "}{privacy && <a href={privacy}>Privacy Policy</a>}</p>
      )}
    </footer>
  );
}
