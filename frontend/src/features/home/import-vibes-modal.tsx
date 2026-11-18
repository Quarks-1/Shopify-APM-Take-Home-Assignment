import { useState } from "react";
import { RiPinterestLine } from "@remixicon/react";
import { Button } from "@/components/base/buttons/button";
import { Input } from "@/components/base/input/input";
import { Modal, ModalBody, ModalFooter, ModalHeader } from "@/components/base/modal/modal";
import { parseBoardUrl } from "@/features/spaces/board-url";
import { useVibeImport } from "@/features/spaces/use-vibe-import";

export interface ImportVibesModalProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ImportVibesModal({ isOpen, onOpenChange }: ImportVibesModalProps) {
  return (
    <Modal size="md" isOpen={isOpen} onOpenChange={onOpenChange}>
      {({ close }) => <ImportVibesForm close={close} />}
    </Modal>
  );
}

function ImportVibesForm({ close }: { close: () => void }) {
  const vibes = useVibeImport();
  const [boardUrl, setBoardUrl] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const result = parseBoardUrl(boardUrl);
  const parsedError = "error" in result ? result.error : null;
  const missingError = boardUrl.trim() === "" ? "Paste a Pinterest board link." : null;
  const errorMessage = parsedError ?? (submitted ? missingError : null);

  const submit = () => {
    setSubmitted(true);
    if (parsedError !== null || missingError !== null || !("url" in result) || result.url === "") return;
    vibes.start(null, result.url);
    close();
  };

  return (
    <form
      className="flex min-h-0 flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      noValidate
    >
      <ModalHeader
        title="Import Vibes Board"
        description="Paste a public Pinterest board link to sort its pins into your Vibes library."
      />
      <ModalBody className="flex flex-col gap-4 pb-1">
        <Input
          label="Pinterest board link"
          isRequired
          autoFocus
          type="url"
          leadingIcon={RiPinterestLine}
          placeholder="pin.it/… or pinterest.com/you/board"
          value={boardUrl}
          onChange={setBoardUrl}
          isInvalid={errorMessage !== null}
          errorMessage={errorMessage ?? undefined}
          hint="You can apply imported boards to any space from Space settings."
        />
      </ModalBody>
      <ModalFooter>
        <Button type="button" variant="secondary" onClick={close}>
          Cancel
        </Button>
        <Button type="submit">Import board</Button>
      </ModalFooter>
    </form>
  );
}
