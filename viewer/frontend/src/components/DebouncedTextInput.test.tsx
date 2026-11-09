import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DebouncedTextInput } from "./DebouncedTextInput";

describe("DebouncedTextInput", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires onChange once, 250ms after the last keystroke", () => {
    const onChange = vi.fn();
    render(<DebouncedTextInput value="" onChange={onChange} aria-label="Job title" />);

    const input = screen.getByRole("textbox", { name: "Job title" });
    fireEvent.change(input, { target: { value: "e" } });
    act(() => vi.advanceTimersByTime(100));
    fireEvent.change(input, { target: { value: "en" } });
    act(() => vi.advanceTimersByTime(100));
    fireEvent.change(input, { target: { value: "eng" } });

    act(() => vi.advanceTimersByTime(249));
    expect(onChange).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(1));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("eng");
  });
});
