import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  // Repeatable params (e.g. location) go out as ?location=a&location=b, not location[]=a.
  paramsSerializer: { indexes: null },
});
