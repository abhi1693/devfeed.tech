"use client";

import { createContext, useContext, type ReactNode } from "react";

const ChartPlaceholderContext = createContext<ReactNode>(null);

/** Keep a chart's selectors mounted while its plot is replaced during a request. */
export const ChartPlaceholder = ChartPlaceholderContext.Provider;
export const useChartPlaceholder = () => useContext(ChartPlaceholderContext);
