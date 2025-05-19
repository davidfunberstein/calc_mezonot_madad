import streamlit as st
import requests
from datetime import datetime, timedelta
import pandas as pd
import xml.etree.ElementTree as ET

# --- הגדרות ---
DATA_GOV_IL_API_URL = "https://api.cbs.gov.il/index/data/price"
CPI_RESOURCE_ID = "120010"
# מקדם קשר שנתי (מספר קבוע, לא אחוז)
ANNUAL_LINKAGE_FACTOR = 1.074

# Set Streamlit page configuration as the very first Streamlit command
st.set_page_config(
    page_title="מחשבון מזונות מוצמד למדד", page_icon="📈", layout="centered"
)

# --- פונקציות עזר לטיפול בתאריכים ---
def get_date_for_cpi_lookup(year, month):
    return f"{year:04d}{month:02d}"

# --- פונקציה לשליפת מדד המחירים לצרכן מהלמ"ס ---
@st.cache_data(ttl=timedelta(hours=12))
def get_cpi_value_and_base(year, month):
    """
    שולפת את ערך מדד המחירים לצרכן ותיאור הבסיס עבור שנה וחודש ספציפיים.
    """
    period_str = get_date_for_cpi_lookup(year, month)
    query_params = {
        "id": CPI_RESOURCE_ID,
        "format": "xml",
        "download": "false",
        "period": period_str,
    }

    try:
        response = requests.get(DATA_GOV_IL_API_URL, params=query_params)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        xml_data = response.text

        root = ET.fromstring(xml_data)

        date_month_elements = root.findall('.//DateMonth')
        date_month_element = None
        for dm_elem in date_month_elements:
            y_elem = dm_elem.find('year')
            m_elem = dm_elem.find('month')
            if y_elem is not None and m_elem is not None and int(y_elem.text) == year and int(m_elem.text) == month:
                date_month_element = dm_elem
                break

        if date_month_element is not None:
            value_element = date_month_element.find('currBase/value')
            base_desc_element = date_month_element.find('currBase/baseDesc')
            month_desc_element = date_month_element.find('monthDesc') # Added for display

            cpi_value = float(value_element.text) if value_element is not None and value_element.text else None
            base_desc = base_desc_element.text if base_desc_element is not None and base_desc_element.text else None
            month_desc = month_desc_element.text if month_desc_element is not None and month_desc_element.text else f"{month:02d}"

            if cpi_value is not None and base_desc is not None:
                return cpi_value, base_desc, month_desc
            else:
                return None, None, None
        else:
            return None, None, None

    except requests.exceptions.RequestException as e:
        st.error(f"שגיאת רשת בעת שליפת נתונים עבור {month:02d}/{year}: {e}")
        return None, None, None
    except ET.ParseError as e:
        st.error(f"שגיאה בניתוח XML עבור {month:02d}/{year}: {e}. תוכן התגובה: {xml_data[:500]}...")
        return None, None, None
    except (ValueError, KeyError, AttributeError) as e: # Added AttributeError for safety
        st.error(f"שגיאה בנתונים שהתקבלו עבור {month:02d}/{year}: {e}")
        return None, None, None


# --- פונקציה לחישוב סכום מוצמד ביחס לבסיס קבוע (הצמדה חוזרת לבסיס) ---
def calculate_indexed_amount_from_fixed_base(
    base_amount,
    fixed_base_cpi_value,        # מדד CPI של נקודת הבסיס הקבועה
    current_period_cpi_value,    # מדד CPI של התקופה הנוכחית
    base_effective_date,         # תאריך התוקף של פסק הדין/ההסכם (לתחשיב מקדם הקשר)
    current_billing_date         # תאריך החיוב בפועל (לתחשיב מקדם הקשר)
):
    """
    מחשבת את הסכום המוצמד מחדש בהתבסס על סכום בסיס קבוע ומדד בסיס קבוע,
    ביחס למדד של התקופה הנוכחית, בתוספת מקדם קשר שנתי.
    הנוסחה: סכום בסיס * (מדד נוכחי / מדד בסיס) * (מקדם קשר)^מספר_שנים
    מקדם הקשר חל החל מה-17 בינואר בכל שנה.
    """
    if (
        fixed_base_cpi_value is None
        or current_period_cpi_value is None
        or fixed_base_cpi_value == 0
    ):
        return None, None # Return None for both amount and multiplier

    # Calculate the CPI-indexed portion first
    cpi_indexed_amount = base_amount * (current_period_cpi_value / fixed_base_cpi_value)

    # Calculate the annual linkage factor based on January 17th
    num_years_for_factor = 0
    
    # Determine the starting year for counting factors
    # The first Jan 17th *after* the base effective date (including Jan 17th of current year if base is before it).
    
    start_year_for_factor_check = base_effective_date.year
    # If base_effective_date is on or after Jan 17th of its year,
    # the first potential annual factor application is Jan 17th of the *next* year.
    if base_effective_date.month > 2 or (base_effective_date.month == 2 and base_effective_date.day >= 28):
        start_year_for_factor_check += 1

    # Iterate from `start_year_for_factor_check` up to the `current_billing_date.year`
    for year_to_check_factor in range(start_year_for_factor_check, current_billing_date.year + 1):
        jan_17_of_check_year = datetime(year_to_check_factor, 2, 28)
        
        # If this January 17th has passed or is today compared to the current_billing_date,
        # then we count this year's factor.
        if jan_17_of_check_year <= current_billing_date:
            num_years_for_factor = 1

    annual_linkage_multiplier = ANNUAL_LINKAGE_FACTOR ** num_years_for_factor
    
    final_amount = cpi_indexed_amount * annual_linkage_multiplier

    return final_amount, annual_linkage_multiplier


# --- פונקציה למציאת חודש המדד הרלוונטי לתאריך אפקטיבי/עדכון ---
def get_cpi_month_for_effective_date(effective_date):
    """
    מחזירה את השנה והחודש של המדד הרלוונטי לתאריך העדכון הנתון.
    המדד מתפרסם ב-15 לחודש עבור חודשיים קודם.
    כלומר, עבור תאריך אפקטיבי/עדכון בחודש X, המדד הרלוונטי הוא של חודש X-2.
    """
    cpi_date = effective_date - pd.DateOffset(months=2)
    
    return cpi_date.year, cpi_date.month


# --- לוגיקה של אפליקציית Streamlit ---
def main():
    st.markdown("<h1 style='text-align: right;'>מחשבון מזונות מוצמד למדד המחירים לצרכן</h1>", unsafe_allow_html=True)
    st.markdown(
        """
        <p style='text-align: right;'>כלי זה מציג את סכום המזונות המעודכן על בסיס מדד המחירים לצרכן.
        <br>
        <strong>הבהרה:</strong> אתר זה מציג מידע חישובי בלבד ואינו מהווה ייעוץ משפטי.
        החישוב בפועל של דמי המזונות ותנאי ההצמדה נקבעים על פי פסק דין או הסכם משפטי,
        וייתכנו הבדלים בהתאם לנסיבות הספציפיות. מומלץ להתייעץ עם עורך דין.
        </p>
        """, unsafe_allow_html=True
    )

    st.header("הגדרות בסיסיות")

    base_mizono_amount = st.number_input(
        "סכום מזונות בסיסי (ש\"ח):",
        min_value=1.0,
        value=4000.0,
        step=100.0,
        format="%.2f",
    )

    col1, col2 = st.columns(2)
    with col1:
        base_effective_month_input = st.selectbox(
            "חודש תוקף פסק הדין/ההסכם (תאריך אפקטיבי):",
            options=list(range(1, 13)),
            index=4,  # מאי (חודש 5)
            format_func=lambda x: datetime(1, x, 1).strftime('%B').replace('January','ינואר').replace('February','פברואר').replace('March','מרץ').replace('April','אפריל').replace('May','מאי').replace('June','יוני').replace('July','יולי').replace('August','אוגוסט').replace('September','ספטמבר').replace('October','אוקטובר').replace('November','נובמבר').replace('December','דצמבר')
        )
    with col2:
        base_effective_year_input = st.number_input(
            "שנת תוקף פסק הדין/ההסכם:",
            min_value=1990,
            max_value=datetime.now().year,
            value=2024,
            step=1,
        )
    
    col3, col4 = st.columns(2)
    with col3:
        update_frequency_months = st.selectbox(
            "תדירות עדכון (חודשים - קובע את נקודות ההצמדה בפועל):", options=[1, 3, 6, 12], index=1
        )
    with col4:
        # New input for billing day
        billing_day_input = st.number_input(
            "יום החיוב של המזונות (קובע מתי מקדם הקשר נכנס לתוקף):",
            min_value=1,
            max_value=31,
            value=1, # Default to 1st of the month
            step=1
        )


    st.divider()
    st.header("תוצאות החישוב")

    if st.button("חשב סכום מזונות מעודכן"):
        with st.spinner("שולף נתונים ומבצע חישוב..."):
            base_effective_year = int(base_effective_year_input)
            base_effective_month = int(base_effective_month_input)
            
            # זהו תאריך תוקף פסק הדין/ההסכם (תאריך אפקטיבי). יום מוגדר כ-1 לטובת חיפוש מדד.
            base_effective_date_obj = datetime(base_effective_year, base_effective_month, 1)
            
            # תאריך החיוב ההתחלתי. נשתמש בו כשילוב של תאריך תוקף ויום החיוב
            try:
                base_billing_date_obj = datetime(base_effective_year, base_effective_month, billing_day_input)
            except ValueError:
                st.error(f"תאריך חיוב לא תקין: {billing_day_input}/{base_effective_month}/{base_effective_year}. וודא שהיום קיים בחודש.")
                return


            # המדד שישמש כ"מדד בסיס קבוע" לכל ההצמדות,
            # נגזר מתאריך התוקף של פסק הדין (חודשיים לפניו).
            fixed_base_cpi_year, fixed_base_cpi_month = get_cpi_month_for_effective_date(base_effective_date_obj)
            
            fixed_base_cpi_value, fixed_base_cpi_base_desc, fixed_base_cpi_month_desc = get_cpi_value_and_base(fixed_base_cpi_year, fixed_base_cpi_month)

            if fixed_base_cpi_value is None:
                st.error(
                    f"שגיאה: לא ניתן היה לשלוף את מדד הבסיס הקבוע עבור {fixed_base_cpi_month_desc} {fixed_base_cpi_year}. "
                    "אנא וודא שתאריך התוקף נבחר כהלכה וכי נתוני המדד זמינים עבור חודש זה (מדד מתפרסם חודשיים אחורה)."
                )
                return

            st.info(f"מדד בסיס קבוע (נגזר מתאריך התוקף {base_effective_month:02d}/{base_effective_year}): **{fixed_base_cpi_value:.2f}** (חודש המדד: {fixed_base_cpi_month_desc} {fixed_base_cpi_year}, בסיס: {fixed_base_cpi_base_desc})")

            # --- חישוב הסכום העדכני ביותר (התוצאה הסופית) ---
            today = datetime.now()
            
            # תאריך חיוב נוכחי עבור החישוב הסופי (בשילוב עם יום החיוב שהוזן)
            try:
                current_billing_date_for_final_calc = datetime(today.year, today.month, billing_day_input)
            except ValueError:
                # If current day is before billing_day_input, use current day for calculation consistency
                # Or, if billing_day_input is not valid for the current month, use last day of month.
                # For simplicity and to match the logic of Jan 17, let's use the first day of the month for CPI lookup
                # and then adjust the billing date to actual billing day for factor calculation.
                current_billing_date_for_final_calc = datetime(today.year, today.month, min(billing_day_input, (datetime(today.year, today.month % 12 + 1, 1) - timedelta(days=1)).day))


            # המדד שיש להצמיד אליו היום, נגזר מתאריך היום (חודשיים לפניו).
            current_period_cpi_year, current_period_cpi_month = get_cpi_month_for_effective_date(today)
            
            current_period_cpi_value_for_final_calc, _, current_period_cpi_month_desc_for_final_calc = get_cpi_value_and_base( # Ignore base_desc for current period, use fixed_base_cpi_base_desc
                current_period_cpi_year, current_period_cpi_month
            )

            # לוגיקה לטיפול במדד חסר עבור החישוב הסופי
            is_estimated_amount = False
            if current_period_cpi_value_for_final_calc is None:
                is_estimated_amount = True
                
                # במקרה של חוסר מדד לחישוב הסופי, נשלוף את המדד האחרון שכן זמין ונשתמש בו
                # כדי להציג את הסכום המוצמד האחרון האפשרי.
                # נסרוק אחורה מתאריך המדד שהיה אמור להתפרסם, עד שנמצא מדד זמין.
                temp_cpi_lookup_date = datetime(current_period_cpi_year, current_period_cpi_month, 1) - pd.DateOffset(months=1) # Start from the month BEFORE the missing CPI
                found_last_cpi = False
                while temp_cpi_lookup_date >= datetime(fixed_base_cpi_year, fixed_base_cpi_month, 1): # Don't go before fixed base CPI month
                    last_available_cpi_value, _, last_available_cpi_month_desc = get_cpi_value_and_base(
                        temp_cpi_lookup_date.year, temp_cpi_lookup_date.month
                    )
                    if last_available_cpi_value is not None:
                        current_period_cpi_value_for_final_calc = last_available_cpi_value
                        current_period_cpi_month_desc_for_final_calc = last_available_cpi_month_desc
                        st.warning(
                            f"אזהרה: המדד לחודש {datetime(current_period_cpi_year, current_period_cpi_month, 1).strftime('%B').replace('January','ינואר').replace('February','פברואר').replace('March','מרץ').replace('April','אפריל').replace('May','מאי').replace('June','יוני').replace('July','יולי').replace('August','אוגוסט').replace('September','ספטמבר').replace('October','אוקטובר').replace('November','נובמבר').replace('December','דצמבר')} {current_period_cpi_year} טרם פורסם. "
                            f"הסכום המוצג הוא הערכה על בסיס המדד האחרון הזמין (חודש מדד {last_available_cpi_month_desc} {temp_cpi_lookup_date.year})."
                        )
                        found_last_cpi = True
                        break
                    temp_cpi_lookup_date -= pd.DateOffset(months=1)
                
                if not found_last_cpi:
                    final_updated_mizono_amount = base_mizono_amount # Fallback to base if no CPI data found at all
                    st.error("לא נמצאו נתוני מדד זמינים לחישוב הסכום העדכני. מציג את סכום הבסיס.")
                    return # Exit if no data
            else:
                st.info(
                    f"מדד עדכון נוכחי (נגזר מהיום): **{current_period_cpi_value_for_final_calc:.2f}** (חודש המדד: {current_period_cpi_month_desc_for_final_calc} {current_period_cpi_year}, בסיס: {fixed_base_cpi_base_desc})"
                )
            
            # חישוב הסכום הסופי ביחס לבסיס הקבוע, כולל מקדם הקשר
            final_updated_mizono_amount, annual_linkage_multiplier_final = calculate_indexed_amount_from_fixed_base(
                base_mizono_amount,
                fixed_base_cpi_value,
                current_period_cpi_value_for_final_calc,
                base_effective_date_obj, # Pass base effective date (start of calculation)
                current_billing_date_for_final_calc # Pass the current billing date (for factor application)
            )
            
            if final_updated_mizono_amount is not None:
                display_amount_message = f"**סכום המזונות המעודכן כיום הוא: {final_updated_mizono_amount:.2f} ש\"ח**"
                if is_estimated_amount:
                    display_amount_message += " (אומדן)"
                st.success(display_amount_message)
                st.info(f"מקדם הצמדה שנתי: {annual_linkage_multiplier_final:.4f} (מבוסס על {ANNUAL_LINKAGE_FACTOR:.3f} לשנה)")

                # חישוב תאריך העדכון הבא המשוער (בהתאם לתדירות שהוזנה)
                next_update_display_date = base_effective_date_obj
                while next_update_display_date <= today:
                    next_update_display_date += pd.DateOffset(months=update_frequency_months)
                
                st.info(f"**תאריך העדכון הבא המשוער (לפי תדירות שהוזנה):** {next_update_display_date.strftime('%d/%m/%Y')}")


                st.subheader("היסטוריית עדכונים מפורטת (לפי תדירות העדכון)")
                st.markdown(
                    """
                    <p style='text-align: right;'>טבלה זו מציגה את סכום המזונות המוצמד בנקודות העדכון, בהתאם לתדירות העדכון שהוזנה.
                    הסכום נשאר קבוע בין נקודות עדכון, והוא מוצמד תמיד למדד הבסיס הקבוע, בתוספת מקדם הקשר השנתי.</p>
                    """, unsafe_allow_html=True
                )

                # בניית טבלה להצגת היסטוריית עדכונים
                history_data = []
                
                current_displayed_amount_in_history = base_mizono_amount 
                
                current_scan_date = base_effective_date_obj
                
                next_update_calc_date = base_effective_date_obj 

                # Limit scanning up to a point slightly beyond today to show future estimated updates
                # Today (May 19, 2025). We want to show up to July 2025 (2 months ahead).
                # The effective date for July would look up May CPI.
                # So max_scan_limit_date should be 2 months from today, at the end of the month.
                max_scan_limit_date = (today + pd.DateOffset(months=2)).replace(day=1) + pd.DateOffset(months=1) - timedelta(days=1)
                
                while current_scan_date <= max_scan_limit_date:
                    is_official_update_point = False
                    if (current_scan_date.year == next_update_calc_date.year and 
                        current_scan_date.month == next_update_calc_date.month):
                        is_official_update_point = True
                    
                    if is_official_update_point:
                        # Construct the billing date for this specific update point
                        try:
                            current_history_billing_date = datetime(current_scan_date.year, current_scan_date.month, billing_day_input)
                        except ValueError:
                            # Handle cases where billing_day_input is invalid for a specific month (e.g., Feb 30)
                            # Default to last day of month
                            current_history_billing_date = datetime(current_scan_date.year, current_scan_date.month, 
                                                            min(billing_day_input, (datetime(current_scan_date.year, current_scan_date.month % 12 + 1, 1) - timedelta(days=1)).day))


                        cpi_for_update_year, cpi_for_update_month = get_cpi_month_for_effective_date(current_scan_date)
                        cpi_for_update_value, _, cpi_for_update_month_desc = get_cpi_value_and_base(
                            cpi_for_update_year, cpi_for_update_month
                        )
                        
                        # Initialize columns for this row
                        base_cpi_val_str = f"{fixed_base_cpi_value:.2f} ({fixed_base_cpi_month_desc} {fixed_base_cpi_year})"
                        current_cpi_val_str = ""
                        cpi_only_change_percent = ""
                        annual_factor_val_str = ""
                        total_change_percent = ""

                        if cpi_for_update_value is None:
                            current_cpi_val_str = f"טרם פורסם ({cpi_for_update_month_desc} {cpi_for_update_year})"
                            # Keep the last calculated amount
                            history_data.append(
                                {
                                    "תאריך עדכון (אפקטיבי)": current_scan_date.strftime("%d/%m/%Y"),
                                    "מדד בסיס": base_cpi_val_str,
                                    "מדד עדכון": current_cpi_val_str,
                                    "שינוי מדד בלבד (%)": "N/A",
                                    "מקדם שנתי": "N/A",
                                    "שינוי כולל (%)": "N/A",
                                    "סכום מעודכן": f"{current_displayed_amount_in_history:.2f} ש\"ח (אומדן - מדד טרם פורסם)",
                                }
                            )
                        else:
                            # If CPI data is available, perform indexation calculation
                            calculated_amount_with_factor, annual_linkage_multiplier_hist = calculate_indexed_amount_from_fixed_base(
                                base_mizono_amount,
                                fixed_base_cpi_value,
                                cpi_for_update_value,
                                base_effective_date_obj,
                                current_history_billing_date # Pass the billing date for this update point
                            )
                            
                            if calculated_amount_with_factor is not None:
                                current_displayed_amount_in_history = calculated_amount_with_factor # Update the displayed amount
                                
                                current_cpi_val_str = f"{cpi_for_update_value:.2f} ({cpi_for_update_month_desc} {cpi_for_update_year})"
                                
                                cpi_only_change_percent = ((cpi_for_update_value / fixed_base_cpi_value) - 1) * 100
                                
                                annual_factor_val_str = f"{annual_linkage_multiplier_hist:.4f}"
                                if annual_linkage_multiplier_hist > 1:
                                    total_change_percent = ((calculated_amount_with_factor / base_mizono_amount) - 1) * 100
                                else:
                                    total_change_percent = cpi_only_change_percent # If no annual factor, total change is just CPI change

                                history_data.append(
                                    {
                                        "תאריך עדכון (אפקטיבי)": current_scan_date.strftime("%d/%m/%Y"),
                                        "מדד בסיס": base_cpi_val_str,
                                        "מדד עדכון": current_cpi_val_str,
                                        "שינוי מדד בלבד (%)": f"{cpi_only_change_percent:.2f}%",
                                        "מקדם שנתי": annual_factor_val_str,
                                        "שינוי כולל (%)": f"{total_change_percent:.2f}%",
                                        "סכום מעודכן": f"{current_displayed_amount_in_history:.2f} ש\"ח",
                                    }
                                )
                            else:
                                st.warning(f"לא ניתן לחשב סכום מעודכן עבור תאריך {current_scan_date.strftime('%d/%m/%Y')}.")
                                break # Stop if calculation fails
                        
                        next_update_calc_date += pd.DateOffset(months=update_frequency_months) # Advance to the next update date

                    else: # If this is not an official update month, the amount remains the same
                        # For non-update points, we still show the last calculated amount
                        history_data.append(
                            {
                                "תאריך עדכון (אפקטיבי)": current_scan_date.strftime("%d/%m/%Y"),
                                "מדד בסיס": "",
                                "מדד עדכון": "",
                                "שינוי מדד בלבד (%)": "",
                                "מקדם שנתי": "",
                                "שינוי כולל (%)": "",
                                "סכום מעודכן": f"{current_displayed_amount_in_history:.2f} ש\"ח", # Displays the last calculated amount
                            }
                        )
                    
                    current_scan_date += pd.DateOffset(months=1) # Advance to the next month
                
                if history_data:
                    df_history = pd.DataFrame(history_data)
                    # Convert to datetime for sorting, then back to string for display
                    df_history['תאריך עדכון (אפקטיבי)'] = pd.to_datetime(df_history['תאריך עדכון (אפקטיבי)'], format="%d/%m/%Y")
                    df_history_sorted = df_history.sort_values(by="תאריך עדכון (אפקטיבי)", ascending=False)
                    df_history_sorted['תאריך עדכון (אפקטיבי)'] = df_history_sorted['תאריך עדכון (אפקטיבי)'].dt.strftime("%d/%m/%Y")
                    
                    st.dataframe(df_history_sorted, hide_index=True)
                else:
                    st.write("אין היסטוריית עדכונים להצגה כרגע בטווח המבוקש.")

# Run the application
if __name__ == "__main__":
    main()
