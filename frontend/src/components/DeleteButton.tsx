/**
 * An admin's delete button for a lead, a run or a campaign. It asks first (the question
 * says what goes and what stays), sends the DELETE, then refreshes every cached answer
 * (the queue, lists and dashboard all change) and calls `onDeleted`. The API's refusal,
 * e.g. "still running; try again shortly", shows next to the button.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, describeError } from "../api/client";

export function DeleteButton({
  path,
  question,
  label = "Delete",
  onDeleted,
}: {
  path: string;
  question: string;
  label?: string;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.delete(path),
    onSuccess: async () => {
      onDeleted?.();
      await queryClient.invalidateQueries();
    },
  });

  return (
    <span className="flex items-center gap-2 text-sm">
      <button
        type="button"
        className="btn px-3 py-1.5 hover:border-(--red)/50 hover:text-(--red)"
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(question)) remove.mutate();
        }}
      >
        {label}
      </button>
      {remove.error && (
        <span role="alert" className="text-(--red)">
          {describeError(remove.error)}
        </span>
      )}
    </span>
  );
}
