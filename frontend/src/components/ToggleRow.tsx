type Props = {
  title: string;
  detail: string;
  enabled: boolean;
  onToggle?: (enabled: boolean) => void;
  readOnly?: boolean;
};

export function ToggleRow({ title, detail, enabled, onToggle, readOnly = false }: Props) {
  return (
    <div className="toggle-row">
      <div>
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
      <button
        className={enabled ? "switch is-on" : "switch"}
        onClick={() => onToggle?.(!enabled)}
        aria-label={readOnly ? "Statut en lecture seule" : (enabled ? "Désactiver" : "Activer")}
        disabled={readOnly}
      >
        <i />
      </button>
    </div>
  );
}
