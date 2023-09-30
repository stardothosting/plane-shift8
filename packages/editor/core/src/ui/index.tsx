"use client"
import * as React from 'react';
import { Extension } from "@tiptap/react";
import { UploadImage } from '@/types/upload-image';
import { DeleteImage } from '@/types/delete-image';
import { getEditorClassNames } from '@/lib/utils';
import { EditorProps } from '@tiptap/pm/view';
import { useEditor } from './hooks/useEditor';
import { EditorContainer } from '@/ui/editor-container';
import { EditorContentWrapper } from '@/ui/editor-content';

interface ITiptapEditor {
  value: string;
  uploadFile: UploadImage;
  deleteFile: DeleteImage;
  noBorder?: boolean;
  borderOnFocus?: boolean;
  customClassName?: string;
  editorContentCustomClassNames?: string;
  onChange?: (json: any, html: string) => void;
  setIsSubmitting?: (isSubmitting: "submitting" | "submitted" | "saved") => void;
  setShouldShowAlert?: (showAlert: boolean) => void;
  editable?: boolean;
  forwardedRef?: any;
  debouncedUpdatesEnabled?: boolean;
  accessValue: string;
  onAccessChange: (accessKey: string) => void;
  commentAccess: {
    icon: string;
    key: string;
    label: "Private" | "Public";
  }[];
  extensions?: Extension[];
  editorProps?: EditorProps;
}

interface TiptapProps extends ITiptapEditor {
  forwardedRef?: React.Ref<EditorHandle>;
}

interface EditorHandle {
  clearEditor: () => void;
  setEditorValue: (content: string) => void;
}

const TiptapEditor = ({
  onChange,
  debouncedUpdatesEnabled,
  editable,
  setIsSubmitting,
  setShouldShowAlert,
  editorContentCustomClassNames,
  value,
  uploadFile,
  deleteFile,
  noBorder,
  borderOnFocus,
  customClassName,
  forwardedRef,
}: TiptapProps) => {
  const editor = useEditor({
    onChange,
    debouncedUpdatesEnabled,
    editable,
    setIsSubmitting,
    setShouldShowAlert,
    value,
    uploadFile,
    deleteFile,
    forwardedRef,
  });

  const editorClassNames = getEditorClassNames({ noBorder, borderOnFocus, customClassName });

  if (!editor) return null;

  return (
    <EditorContainer editor={editor} editorClassNames={editorClassNames}>
      <div className="flex flex-col">
        <EditorContentWrapper editor={editor} editorContentCustomClassNames={editorContentCustomClassNames} />
      </div>
    </EditorContainer >
  );
};

const TiptapEditorWithRef = React.forwardRef<EditorHandle, ITiptapEditor>((props, ref) => (
  <TiptapEditor {...props} forwardedRef={ref} />
));

TiptapEditorWithRef.displayName = "TiptapEditorWithRef";

export { TiptapEditor, TiptapEditorWithRef };
